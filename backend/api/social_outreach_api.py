"""
HTTP surface for the social outreach loop.

Endpoints
---------
GET  /api/social-outreach/status              — enabled/cadence/per-platform posting modes snapshot
POST /api/social-outreach/enable              — flip global on
POST /api/social-outreach/kill                — flip global off (hard stop)
POST /api/social-outreach/stop-posting        — stop ALL public posting (outreach + Connections), hold what is queued
POST /api/social-outreach/resume-posting      — lift that stop; held items still need their usual approval
GET  /api/social-outreach/audit?limit=200     — recent log rows
GET  /api/social-outreach/queue               — drafts waiting for a person's approval
POST /api/social-outreach/drafts              — create a draft manually from the UI
PATCH /api/social-outreach/drafts/<id>        — save edits to a draft (status='drafted' only)
POST /api/social-outreach/approve/<id>        — approve a queued draft (409 with the reason for X and LinkedIn)
POST /api/social-outreach/reject/<id>         — reject and mark won't-post (409 once the post is being submitted)
GET  /api/social-outreach/post-now            — Agent Screen presence, the watched post running, approved drafts waiting for one
POST /api/social-outreach/post-now/<id>       — "Post now, I'm watching": start the one browser post for an approved draft
POST /api/social-outreach/claim/<id>          — the Discord bot takes an approved Discord draft (approved → processing)
POST /api/social-outreach/submit/<id>         — the Discord bot is about to send (processing → submitting); 409 means do not send
POST /api/social-outreach/thread-fit          — internal: ask the thread-fit judge whether a message is worth a reply
POST /api/social-outreach/draft-comment       — internal: draft a reply via the LLM, with grade
POST /api/social-outreach/scout-url           — agent fetches OP + comments for a URL so the human doesn't paste them
POST /api/social-outreach/run-pass            — fire a Reddit / self-share pass on demand instead of waiting for the cron

The draft-comment endpoint is the LLM call site for both the Discord cog and
the Reddit browser loop. It uses Ollama directly with the persona block — no
chat session, no streaming, no tools. One-shot completion that returns
{draft, grade, reason}.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from backend.services.social_outreach import (
    audit,
    external_grader,
    gates,
    kill_switch,
    persona,
    platforms,
    profile,
)
from backend.utils.hosts import host_matches

logger = logging.getLogger(__name__)

social_outreach_bp = Blueprint("social_outreach", __name__, url_prefix="/api/social-outreach")


# --- Status / control ----------------------------------------------------

@social_outreach_bp.get("/status")
def status():
    return jsonify(kill_switch.status_snapshot())


@social_outreach_bp.post("/enable")
def enable():
    kill_switch.set_enabled(True)
    return jsonify({"enabled": True})


@social_outreach_bp.post("/kill")
def kill():
    return jsonify(kill_switch.apply_kill_switch())


@social_outreach_bp.post("/stop-posting")
def stop_posting():
    """Stop all public posting: outreach and Connections publishing alike."""
    result = kill_switch.stop_all_posting()
    if not result.get("posting_stopped"):
        return jsonify({**result, "error": "the stop could not be saved; posting is not stopped"}), 500
    return jsonify(result)


@social_outreach_bp.post("/resume-posting")
def resume_posting():
    result = kill_switch.resume_posting()
    if result.get("posting_stopped"):
        return jsonify({**result, "error": "the stop could not be lifted; posting is still stopped"}), 500
    return jsonify(result)


# --- Audit / queue / approve ---------------------------------------------

@social_outreach_bp.get("/audit")
def get_audit():
    limit = min(int(request.args.get("limit", 200)), 1000)
    from backend.models import SocialOutreachLog
    rows = (
        SocialOutreachLog.query
        .order_by(SocialOutreachLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return jsonify([r.to_dict() for r in rows])


@social_outreach_bp.get("/queue")
def get_queue():
    """Drafts waiting for a person's approval."""
    from backend.models import SocialOutreachLog
    rows = (
        SocialOutreachLog.query
        .filter(SocialOutreachLog.status == "drafted")
        .order_by(SocialOutreachLog.created_at.desc())
        .limit(200)
        .all()
    )
    return jsonify([r.to_dict() for r in rows])


@social_outreach_bp.get("/approved")
def get_approved():
    """Approved drafts not posted yet: Discord ones wait for the Discord bot,
    Reddit, YouTube and Facebook ones for a person's "Post now" (/post-now)."""
    from backend.models import SocialOutreachLog
    rows = (
        SocialOutreachLog.query
        .filter(SocialOutreachLog.status == "approved")
        .order_by(SocialOutreachLog.created_at.asc())
        .limit(50)
        .all()
    )
    return jsonify([r.to_dict() for r in rows])


@social_outreach_bp.post("/drafts")
def create_draft():
    """Create a new draft from manual UI input. Lands in the queue as status='drafted'.

    Body: {platform, action?, target_url?, target_thread_id?, draft_text?, grade_score?,
           source?, reason?}
    Returns the new SocialOutreachLog row.
    """
    body = request.get_json(silent=True) or {}
    platform = (body.get("platform") or "").strip()
    if not platform:
        return jsonify({"error": "platform required"}), 400
    refusal = platforms.refusal_for(platform)
    if refusal:
        return jsonify({"error": refusal}), 400
    action = (body.get("action") or "comment").strip()
    draft_text = body.get("draft_text") or ""
    target_url = body.get("target_url") or None
    target_thread_id = body.get("target_thread_id") or None

    grade_score = body.get("grade_score")
    if grade_score is not None:
        try:
            grade_score = float(grade_score)
        except (TypeError, ValueError):
            grade_score = None

    # Reuse the audit pipeline so the manual draft hits jsonl + DB the same
    # way an LLM-drafted row does. Marks "source=manual_ui" unless the caller
    # names itself (outreach_draft_post sends "chat_tool"), so we can later
    # tell hand-rolled drafts apart from the cron-fed ones.
    extra = {"source": body.get("source") or "manual_ui"}
    if body.get("reason"):
        extra["reason"] = body["reason"]
    audit_id = audit.log_outreach_event(
        platform=platform,
        action=action,
        target_url=target_url,
        target_thread_id=target_thread_id,
        draft_text=draft_text,
        status="drafted",
        grade_score=grade_score,
        extra=extra,
    )
    if audit_id is None:
        return jsonify({"error": "failed to persist draft"}), 500

    from backend.models import SocialOutreachLog
    row = SocialOutreachLog.query.get(audit_id)
    if row is None:
        return jsonify({"error": "draft persisted but row not retrievable"}), 500
    return jsonify(row.to_dict()), 201


@social_outreach_bp.patch("/drafts/<int:event_id>")
def update_draft(event_id: int):
    """Save edits to a draft without approving it. Only valid while status='drafted'.

    Body: {draft_text?, target_url?, target_thread_id?, platform?, action?}
    Approved/posted/rejected rows are immutable — edit before you ship.
    """
    from backend.models import SocialOutreachLog, db
    row = SocialOutreachLog.query.get(event_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    if row.status != "drafted":
        return jsonify({"error": f"cannot edit a draft in status '{row.status}'"}), 409

    body = request.get_json(silent=True) or {}
    refusal = platforms.refusal_for(body.get("platform"))
    if refusal:
        return jsonify({"error": refusal}), 400
    if "draft_text" in body:
        row.draft_text = body["draft_text"] or ""
    if "target_url" in body:
        row.target_url = body["target_url"] or None
    if "target_thread_id" in body:
        row.target_thread_id = body["target_thread_id"] or None
    if body.get("platform"):
        row.platform = body["platform"]
    if body.get("action"):
        row.action = body["action"]
    db.session.commit()
    return jsonify(row.to_dict())


@social_outreach_bp.post("/approve/<int:event_id>")
def approve(event_id: int):
    from backend.models import SocialOutreachLog
    from backend.services.social_outreach import transitions

    body = request.get_json(silent=True) or {}
    if not transitions.approve(event_id, body.get("draft_text")):
        status = transitions.current_status(event_id)
        if status is None:
            return jsonify({"error": "not found"}), 404
        return jsonify({"error": transitions.approval_refusal(event_id), "status": status}), 409
    row = SocialOutreachLog.query.get(event_id)
    # "next" tells the person what happens now; approving posts nothing.
    return jsonify({**row.to_dict(), "next": platforms.after_approval(row.platform)})


@social_outreach_bp.post("/reject/<int:event_id>")
def reject(event_id: int):
    """Reject a draft so it never posts.

    A 200 is a guarantee: the row is rejected and no poster will publish it,
    including a row a poster had already claimed (``rejected_from`` is then
    "processing"). Once the publishing step has started the answer is 409 and
    the row is left as it is.
    """
    from backend.models import SocialOutreachLog
    from backend.services.social_outreach import transitions

    outcome = transitions.reject(event_id)
    if outcome.status is None:
        return jsonify({"error": "not found"}), 404
    if not outcome.rejected:
        return jsonify({
            "error": transitions.reject_refusal(outcome.status),
            "status": outcome.status,
        }), 409
    row = SocialOutreachLog.query.get(event_id)
    return jsonify({**row.to_dict(), "rejected_from": outcome.status})


# --- Watched posts: Reddit, YouTube, Facebook --------------------------------

@social_outreach_bp.get("/post-now")
def watched_post_status():
    """What the Approvals page needs to offer "Post now, I'm watching".

    {watching, watching_reason, running, last, waiting}: whether the Agent
    Screen viewer is streaming, the post running now, how the last one ended,
    and the approved drafts that wait for a person to start them.
    """
    from sqlalchemy import func

    from backend.models import SocialOutreachLog
    from backend.services.social_outreach import api_post, watched_post

    rows = (
        SocialOutreachLog.query
        .filter(SocialOutreachLog.status == "approved")
        .filter(func.lower(SocialOutreachLog.platform).in_(platforms.platforms_in(platforms.WATCHED)))
        .order_by(SocialOutreachLog.created_at.asc())
        .limit(50)
        .all()
    )
    waiting = [r.to_dict() for r in rows if platforms.posts(r.platform, r.action)]
    return jsonify({**watched_post.status(), "waiting": waiting, "api_platforms": api_post.ready_platforms()})


@social_outreach_bp.post("/post-now/<int:event_id>")
def post_now(event_id: int):
    """Start the one post for an approved draft ("Post now, I'm watching").

    202 when it is queued; the agent then drives its browser on the Agent
    Screen. A YouTube account connected for comments has already posted by
    then, through the official API. 409 with the reason when it may not start
    (the Agent Screen is not open, another post is running, a limit or switch
    says no).
    """
    from backend.services.social_outreach import watched_post

    outcome = watched_post.start(event_id)
    if outcome.status is None:
        return jsonify({"error": "not found"}), 404
    if not outcome.started:
        return jsonify({"error": outcome.message, "status": outcome.status}), 409
    return jsonify({"started": True, "event_id": event_id, "message": outcome.message}), 202


def _bot_only_refusal(event_id: int) -> Optional[str]:
    """Why a poster outside the backend may not take this row, or None.

    Only the Discord bot posts from outside (platforms.BOT). A Reddit, YouTube
    or Facebook draft posts only through a person's watched post, so no caller
    can claim one and drain the approved list.
    """
    from backend.models import SocialOutreachLog, db

    found = (
        db.session.query(SocialOutreachLog.platform)
        .filter(SocialOutreachLog.id == event_id)
        .first()
    )
    if found is None:
        return None
    policy = platforms.policy_for(found[0])
    if policy is None:
        return f"nothing here posts to '{found[0]}'"
    if policy.mode == platforms.REFUSED:
        return policy.refusal
    if policy.mode == platforms.WATCHED:
        return (
            f"{policy.label} drafts post only from the Approvals page, one at a time, when a "
            "person presses Post now with the Agent Screen open"
        )
    return None


def _poster_step(event_id: int, step, verb: str, needs: str):
    """Run a poster's transition (claim or submit) and answer for its route."""
    from backend.models import SocialOutreachLog
    from backend.services.social_outreach import transitions

    refusal = _bot_only_refusal(event_id)
    if refusal:
        return jsonify({"error": refusal, "status": transitions.current_status(event_id)}), 409
    if not step(event_id):
        status = transitions.current_status(event_id)
        if status is None:
            return jsonify({"error": "not found"}), 404
        stopped = kill_switch.posting_stop_reason()
        if stopped:
            return jsonify({"error": stopped, "status": status}), 409
        return jsonify({
            "error": f"cannot {verb} from status '{status}' (only from {needs})",
            "status": status,
        }), 409
    return jsonify(SocialOutreachLog.query.get(event_id).to_dict())


@social_outreach_bp.post("/claim/<int:event_id>")
def claim(event_id: int):
    """Atomically claim an approved Discord draft for posting (approved → processing).

    Used by the Discord cog so overlapping polls cannot double-post the same
    row. Drafts for any other platform are refused (_bot_only_refusal).
    """
    from backend.services.social_outreach import transitions

    return _poster_step(event_id, transitions.claim, "claim", "approved")


@social_outreach_bp.post("/submit/<int:event_id>")
def begin_submit(event_id: int):
    """Commit a claimed draft to being published (processing → submitting).

    The Discord cog calls this immediately before it sends. A 409 means the
    draft was rejected after the claim and must not be sent.
    """
    from backend.services.social_outreach import transitions

    return _poster_step(event_id, transitions.begin_submit, "submit", "processing")


# --- Thread-fit judge, for callers outside this process ------------------

@social_outreach_bp.post("/thread-fit")
def thread_fit():
    """
    Body: {
        "text": "the message or post",        # required
        "title": "Discord message in #help",  # optional
        "context": ["earlier message", ...],  # optional, oldest first; the judge reads the last three
        "feature_hint": "voice",              # optional: what the caller's keyword filter matched
    }
    Returns external_grader.score_thread_relevance's answer plus ``unfit``
    (recon.judged_unfit: a grade below MIN_RELEVANCE_GRADE, or a "skip"
    verdict) and the ``feature_hint`` the message was judged against: the
    first outreach-profile feature named in the text, else the caller's hint.

    The same judge recon and the Reddit loop ask before drafting. A judge that
    could not run comes back skipped and not unfit; the caller drafts and
    passes relevance_unchecked to /draft-comment. Nothing is drafted or
    recorded here.
    """
    from backend.services.social_outreach.recon import judged_unfit

    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    title = str(body.get("title") or "")
    context = [str(c) for c in (body.get("context") or []) if c]
    feature_hint = (
        persona.find_relevant_feature("\n".join([title, text, *context]))
        or str(body.get("feature_hint") or "").strip()
    )
    relevance = external_grader.score_thread_relevance(
        title=title,
        selftext=text,
        top_comments=context[-3:],
        feature_hint=feature_hint,
    )
    return jsonify({**relevance, "unfit": judged_unfit(relevance), "feature_hint": feature_hint})


# --- Draft-comment endpoint (the LLM call site) --------------------------

@social_outreach_bp.post("/draft-comment")
def draft_comment():
    """
    Body: {
        "platform": "reddit"|"discord"|"facebook",
        "thread_context": "OP + top comments concatenated",
        "target_url": "https://...",
        "target_thread_id": "abc123",  # optional, for dedupe
        "feature_hint": "video_gen",   # optional: a person's pick for the draft to lead with
        "task_id": 42,                 # optional, links audit row to celery task
        "mode": "comment"|"share",     # default "comment"
        "share_target": "r/SideProject",  # required for share mode
        "share_link": "https://example.com",  # share mode; defaults to the profile's site
        "relevance_unchecked": true,   # optional: the thread-fit judge could not run
    }
    Returns: {draft, grade, reason, audit_id, would_post, gates}

    Every draft is queued as drafted for a person's approval; ``would_post``
    is always false and stays in the answer for the callers that read it.
    gates.independent_check is passed, not_independent, failed or
    unavailable: whether the second-opinion grader ran on this draft and what
    it concluded (not_independent: it passed, but the grader is the drafter's
    own model), recorded for the person who reviews it. A share is never
    graded (there is no thread for the rubric). A draft for X or LinkedIn is
    refused (400).
    """
    body = request.get_json(silent=True) or {}
    platform = body.get("platform", "unknown")
    refusal = platforms.refusal_for(platform)
    if refusal:
        return jsonify({"error": refusal}), 400
    mode = body.get("mode", "comment")
    action = "comment" if mode == "comment" else "share"
    task_id = body.get("task_id")
    target_url = body.get("target_url")
    target_thread_id = body.get("target_thread_id")
    relevance_unchecked = bool(body.get("relevance_unchecked"))

    if mode == "share":
        context = {
            "target": body.get("share_target") or "(unspecified)",
            "link_url": body.get("share_link") or profile.site_url(),
        }
    else:
        thread_context = body.get("thread_context", "")
        if not thread_context:
            return jsonify({"error": "thread_context required"}), 400
        context = {
            "thread_context": thread_context,
            "url": target_url,
        }

    result = persona.draft_outreach_text(
        platform=platform,
        context=context,
        tone=body.get("tone"),
        mode=mode,
        requested_feature=body.get("feature_hint"),
    )
    
    draft_text = result.get("draft", "")
    grade = result.get("grade", 0.0)
    reason = result.get("reason", "")

    enabled = kill_switch.is_enabled()
    cadence_ok, cadence_reason = kill_switch.cadence_allows_post(platform)
    grade_ok = grade >= 0.7
    has_draft = bool(draft_text.strip())

    # Second opinion from a different model, blind to the self-grade. An empty
    # draft cannot post, so it is not sent to the grader. The rubric judges a
    # reply to a thread; a share has none, so it is not graded and gates holds it.
    if action == "share":
        ext = {"grade": 0.0, "checked": False, "skipped": True, "model": None, "reason": "share_not_graded"}
    elif has_draft:
        ext = external_grader.grade_draft_externally(draft_text, context["thread_context"])
    else:
        ext = {"grade": 0.0, "checked": False, "skipped": True, "model": None, "reason": "empty_draft"}
    independent_pass, independent_reason = gates.independent_ok(
        ext, action=action, relevance_unchecked=relevance_unchecked,
    )
    independent_check = gates.independent_check_label(ext)
    hold_reason = None if independent_pass else independent_reason

    # Every draft waits for a person; nothing here approves one.
    would_post = False

    audit_id = audit.log_outreach_event(
        platform=platform,
        action=action,
        target_url=target_url,
        target_thread_id=target_thread_id,
        draft_text=draft_text,
        posted_text=None,
        status="drafted",
        grade_score=grade,
        abort_reason=None,
        task_id=task_id,
        extra={
            "reason": reason,
            "would_post": would_post,
            "cadence_block": cadence_reason if not cadence_ok else None,
            "independent_check": independent_check,
            "external_grade": ext.get("grade"),
            "external_passed": ext.get("passed"),
            "external_reason": ext.get("reason", ""),
            "external_model": ext.get("model"),
            "drafter_model": ext.get("drafter_model"),
            "external_independent": ext.get("independent"),
            "relevance_unchecked": relevance_unchecked,
            "hold_reason": hold_reason,
        },
    )

    return jsonify({
        "audit_id": audit_id,
        "draft": draft_text,
        "grade": grade,
        "reason": reason,
        "would_post": would_post,
        "gates": {
            "enabled": enabled,
            "supervised": True,
            "cadence_ok": cadence_ok,
            "cadence_reason": cadence_reason,
            "grade_ok": grade_ok,
            "has_draft": has_draft,
            "independent_check": independent_check,
            "independent_reason": independent_reason,
        },
    })


# --- Snippet bank + citation helper (powering the OutreachPage widgets) ---

@social_outreach_bp.get("/snippets")
def get_snippets():
    """Copy blocks for the snippet bank in the Drafting zone, read from the
    local profile: empty on a fresh install until "What you promote" is filled."""
    current = profile.load()
    return jsonify({
        "configured": current.is_configured(),
        "name": current.name,
        "pitch": current.pitch,
        "site_url": current.site_url,
        "repo_url": current.repo_url,
        "videos": current.videos,
        "feature_blurbs": current.feature_blurbs(),
        "tones": list(persona.TONE_GUIDES.keys()),
    })


# --- "What you promote" profile --------------------------------------------

@social_outreach_bp.get("/profile")
def get_profile():
    """The local outreach profile (data/social_outreach/profile.json)."""
    current = profile.load()
    return jsonify({"profile": current.to_dict(), "configured": current.is_configured()})


@social_outreach_bp.put("/profile")
def put_profile():
    """Replace the profile. Body: the profile object (see profile.py for the
    fields); URLs must be http(s), lists and texts are capped. 400 with a plain
    message when a value is not accepted."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "profile object required"}), 400
    try:
        saved = profile.save(body.get("profile", body))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except OSError as e:
        logger.warning("outreach profile save failed: %s", e)
        return jsonify({"error": f"could not write the profile: {e}"}), 500
    return jsonify({"profile": saved.to_dict(), "configured": saved.is_configured()})


@social_outreach_bp.post("/fetch-meta")
def fetch_meta():
    """
    Quick metadata fetcher for the citation tool. Pulls page title +
    og:description + hostname so you can drop a clean reference into a draft.

    Body: {"url": "https://..."}
    """
    import ipaddress
    import re
    import socket
    from urllib.parse import urljoin, urlparse

    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return jsonify({"error": "url must include http(s)://"}), 400

    # SSRF guard. Without this, /fetch-meta would happily proxy a request to
    # http://localhost:5432, http://169.254.169.254/latest/meta-data, or any
    # internal-network host the backend can reach. We resolve the hostname
    # ourselves and reject anything in private/loopback/link-local space.
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return jsonify({"error": "url has no hostname"}), 400
    try:
        addr_infos = socket.getaddrinfo(host, None)
        for info in addr_infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return jsonify({"error": "url resolves to a private/internal address"}), 400
    except (socket.gaierror, ValueError) as e:
        return jsonify({"error": f"could not resolve hostname: {e}"}), 400

    try:
        import requests as _r
        headers = {"User-Agent": "guaardvark-outreach/0.1 citation-fetcher"}
        resp = _r.get(url, headers=headers, timeout=10, allow_redirects=False)

        # One-hop redirect with SSRF re-validation. Without this, t.co / bit.ly
        # and friends return empty meta because the 3xx response body is empty.
        # We follow exactly one hop and re-check the destination against the
        # same private-IP guard so a redirect can't slip out to an internal host.
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if location:
                next_url = urljoin(url, location)
                next_parsed = urlparse(next_url)
                if next_parsed.scheme not in ("http", "https"):
                    return jsonify({"error": "redirect to non-http(s) blocked"}), 400
                next_host = next_parsed.hostname
                if not next_host:
                    return jsonify({"error": "redirect has no hostname"}), 400
                try:
                    for info in socket.getaddrinfo(next_host, None):
                        ip = ipaddress.ip_address(info[4][0])
                        if (ip.is_private or ip.is_loopback or ip.is_link_local
                                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                            return jsonify({"error": "redirect resolves to a private/internal address"}), 400
                except (socket.gaierror, ValueError) as e:
                    return jsonify({"error": f"redirect host unresolvable: {e}"}), 400
                resp = _r.get(next_url, headers=headers, timeout=10, allow_redirects=False)
                url = next_url

        html = resp.text[:200_000]
    except Exception as e:
        return jsonify({"error": f"fetch failed: {e}"}), 502

    title = ""
    description = ""

    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I | re.S)
        if m:
            title = m.group(1).strip()

    m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m:
        description = m.group(1).strip()
    if not description:
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if m:
            description = m.group(1).strip()

    return jsonify({
        "url": url,
        "hostname": urlparse(url).hostname or "",
        "title": title[:300],
        "description": description[:600],
    })


# --- Agent-driven helpers: scout URLs, fire passes on demand --------------

def _suggest_platform_from_host(host: str) -> Optional[str]:
    """Map a hostname to one of the queue's known platform slugs."""
    if host_matches(host, "reddit.com"):
        return "reddit"
    if host_matches(host, "discord.com", "discord.gg"):
        return "discord"
    if host_matches(host, "facebook.com", "fb.com"):
        return "facebook"
    return None


_REDDIT_HOSTS = frozenset({"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com", "m.reddit.com"})


def _reddit_thread_path(url: str) -> Optional[str]:
    """Path of a reddit.com thread URL, or None when the host is not Reddit.

    The host is matched exactly against ``_REDDIT_HOSTS`` so the JSON fetch can
    be issued to a fixed host; a substring check would accept look-alikes such as
    ``reddit.com.example.net``.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in _REDDIT_HOSTS:
        return None
    return parsed.path or "/"


def _scout_reddit_url(url: str) -> Optional[Dict[str, Any]]:
    """For a Reddit thread URL, pull OP + top-5 comments via the public JSON API.

    Reuses fetch_thread_comments-style parsing so the agent ends up with the
    same context the cron loop already gets — no humans pasting OPs into the
    modal Topic field.
    """
    import re
    import requests

    # Reddit's JSON endpoint accepts the same path with .json appended. Strip
    # any trailing slash + querystring before tacking it on.
    path = _reddit_thread_path(url)
    if path is None:
        return None
    json_url = "https://www.reddit.com" + path.rstrip("/") + ".json?limit=10&depth=1"
    headers = {"User-Agent": "guaardvark-outreach/0.1 scout"}
    try:
        resp = requests.get(json_url, headers=headers, timeout=10, allow_redirects=False)
        if not resp.ok:
            return None
        data = resp.json()
    except Exception as e:  # network/JSON failure — caller falls back to generic scrape
        logger.warning("reddit scout failed for %s: %s", url, e)
        return None

    if not isinstance(data, list) or len(data) < 2:
        return None
    try:
        op = data[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return None

    title = (op.get("title") or "").strip()
    selftext = (op.get("selftext") or "").strip()
    thread_id = op.get("id") or ""
    subreddit = op.get("subreddit") or ""

    comments: list[str] = []
    for entry in data[1].get("data", {}).get("children", []):
        if entry.get("kind") != "t1":
            continue
        body = ((entry.get("data") or {}).get("body") or "").strip()
        if body and body not in ("[deleted]", "[removed]"):
            comments.append(body)
        if len(comments) >= 5:
            break

    blocks = [f"r/{subreddit} — {title}"] if subreddit else [title]
    if selftext:
        blocks.append(f"OP body:\n{selftext[:1500]}")
    if comments:
        blocks.append("Top comments:\n" + "\n".join(f"- {c[:600]}" for c in comments))

    return {
        "title": title,
        "description": selftext[:300],
        "hostname": "reddit.com",
        "thread_context": "\n\n".join(blocks),
        "target_thread_id": thread_id,
        "suggested_platform": "reddit",
        "source": "reddit_json",
    }


def _scout_generic_url(url: str) -> Dict[str, Any]:
    """SSRF-guarded HTML fetch + extract title, og:description, and the first
    few <p> tags as a coarse thread_context. Mirrors /fetch-meta's safety rails
    but returns the richer "thread_context" shape the modal wants.
    """
    import ipaddress
    import re
    import socket
    from urllib.parse import urljoin, urlparse

    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return {"error": "url has no hostname"}, 400  # type: ignore[return-value]
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return {"error": "url resolves to a private/internal address"}, 400  # type: ignore[return-value]
    except (socket.gaierror, ValueError) as e:
        return {"error": f"could not resolve hostname: {e}"}, 400  # type: ignore[return-value]

    try:
        import requests as _r
        # Browser-like UA. The previous "guaardvark-outreach/0.1 scout" string
        # was a tell — YouTube (and others) serve a stripped-down placeholder
        # to obvious bots, which made every YouTube watch URL come back with
        # empty og:title/description. The persona then drafted ungrounded
        # comments. A real browser UA gets us the actual server-rendered meta.
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                "Gecko/20100101 Firefox/128.0"
            ),
            "Accept-Language": "en-US,en;q=0.5",
        }
        resp = _r.get(url, headers=headers, timeout=10, allow_redirects=False)

        # Single-hop redirect with re-validated host (same guard as fetch_meta).
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if location:
                next_url = urljoin(url, location)
                next_parsed = urlparse(next_url)
                if next_parsed.scheme not in ("http", "https"):
                    return {"error": "redirect to non-http(s) blocked"}, 400  # type: ignore[return-value]
                next_host = next_parsed.hostname
                if not next_host:
                    return {"error": "redirect has no hostname"}, 400  # type: ignore[return-value]
                try:
                    for info in socket.getaddrinfo(next_host, None):
                        ip = ipaddress.ip_address(info[4][0])
                        if (ip.is_private or ip.is_loopback or ip.is_link_local
                                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                            return {"error": "redirect resolves to a private/internal address"}, 400  # type: ignore[return-value]
                except (socket.gaierror, ValueError) as e:
                    return {"error": f"redirect host unresolvable: {e}"}, 400  # type: ignore[return-value]
                resp = _r.get(next_url, headers=headers, timeout=10, allow_redirects=False)
                url = next_url
        # 2MB cap (was 200KB). YouTube watch pages have ~600KB of inline JSON
        # before the meta tags in <head>, so the old cap silently dropped the
        # og:title for every video URL. Cap is still bounded so a runaway page
        # can't OOM us; the regex is anchored on `<meta` so it's linear in cap.
        html = resp.text[:2_000_000]
    except Exception as e:
        return {"error": f"fetch failed: {e}"}, 502  # type: ignore[return-value]

    title = ""
    description = ""
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I | re.S)
        if m:
            title = m.group(1).strip()

    m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m:
        description = m.group(1).strip()
    if not description:
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if m:
            description = m.group(1).strip()

    # Strip out scripts/styles before grabbing paragraphs, otherwise we get
    # noisy JS blobs that the LLM hates riffing on.
    body_html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    paragraphs = re.findall(r"<p[^>]*>(.+?)</p>", body_html, flags=re.I | re.S)
    cleaned = []
    for p in paragraphs[:8]:
        text = re.sub(r"<[^>]+>", " ", p)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 40:
            cleaned.append(text)
        if len(cleaned) >= 5:
            break

    blocks = []
    if title:
        blocks.append(f"Title: {title}")
    if description:
        blocks.append(f"Description: {description}")
    if cleaned:
        blocks.append("Excerpt:\n" + "\n\n".join(cleaned))

    hostname = urlparse(url).hostname or ""
    return {
        "title": title[:300],
        "description": description[:600],
        "hostname": hostname,
        "thread_context": "\n\n".join(blocks)[:6000],
        "suggested_platform": _suggest_platform_from_host(hostname),
        "source": "html_scrape",
    }


@social_outreach_bp.post("/scout-url")
def scout_url():
    """Fetch context for a URL automatically so the human doesn't have to
    paste OP + top comments into the New Draft modal.

    Body: {url, force_dom?: bool}
    Returns: {title, description, hostname, thread_context, target_thread_id?,
              suggested_platform?, source}

    Routing:
      reddit.com → JSON API (fast, no Firefox round-trip)
      discord/facebook → drive the agent's logged-in Firefox via BiDi
      X and LinkedIn → refused (platforms.REFUSED): nothing here reads or posts there
      anything else → SSRF-guarded HTML scrape (the original cheap path)
    """
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    force_dom = bool(body.get("force_dom"))
    if not url or not url.startswith(("http://", "https://")):
        return jsonify({"error": "url must include http(s)://"}), 400
    refusal = platforms.refusal_for_url(url)
    if refusal:
        return jsonify({"error": refusal}), 400

    parsed_host = ""
    try:
        from urllib.parse import urlparse
        parsed_host = (urlparse(url).hostname or "").lower()
    except Exception:
        pass

    # Reddit JSON API — primary path unless caller forces DOM.
    if _reddit_thread_path(url) is not None and not force_dom:
        scouted = _scout_reddit_url(url)
        if scouted:
            return jsonify(scouted)
        # JSON failed (rate-limited, verification wall, etc) — fall through to DOM.

    # Drive Firefox via BiDi for the platforms whose pages are JS shells. The
    # agent's session inherits the user's logged-in cookies on display restart,
    # so this works for Discord/Facebook without any auth dance.
    from backend.services.dom_metadata_extractor import DOMMetadataExtractor
    extractor = DOMMetadataExtractor.get_instance()
    platform = extractor.detect_platform(url)
    if platform in ("discord", "facebook", "reddit"):
        dom_result = extractor.extract_thread_context(url, platform=platform)
        if "error" not in dom_result:
            return jsonify(dom_result)
        # CDP path failed — surface what we got so the caller can see it,
        # then fall through to the generic HTML scrape as the last resort.
        logger.warning("CDP scout failed for %s: %s", url, dom_result.get("error"))

    # Generic HTML scrape — works for blogs/forums where there's no platform
    # extractor and no need to drive a logged-in browser.
    result = _scout_generic_url(url)
    if isinstance(result, tuple):  # error path returns (dict, status_code)
        body, code = result
        return jsonify(body), code
    return jsonify(result)


@social_outreach_bp.post("/intent")
def execute_intent():
    """Classify natural-language outreach intent, then dispatch to real ops.

    Body: {
      text?: "comment on youtube videos regarding Offline AI or ComfyUI",
      platform?: "youtube"|"reddit",
      action?: "comment"|"recon"|"draft"|"share",
      topics?: ["Offline AI", "ComfyUI"],
      max_candidates?: 5,
      chain_draft?: true
    }

    Freeform text is LLM-classified (status / list_queue / approve / reject /
    scout_and_draft / refuse). Scout jobs are queued only for scout_and_draft
    with real topics + confidence. Refuse and status answers do not queue.
    Explicit platform+topics skip the classifier (structured override).
    """
    body = request.get_json(silent=True) or {}
    from backend.services.social_outreach.intent import execute_outreach_intent

    result = execute_outreach_intent(
        text=body.get("text") or "",
        platform=body.get("platform"),
        action=body.get("action"),
        topics=body.get("topics"),
        max_candidates=body.get("max_candidates"),
        chain_draft=body.get("chain_draft"),
        created_by=body.get("created_by") or "api_intent",
    )
    # Refuse / clarify: HTTP 200 so clients can read message without treating
    # it as a transport failure. Kill-switch blocks stay 403; other hard
    # failures (missing draft, bad transition) stay 400.
    if result.get("ok"):
        status = 200
    elif result.get("refused"):
        status = 200
    elif result.get("error") and "disabled" in (result.get("error") or "").lower():
        status = 403
    else:
        status = 400
    return jsonify(result), status


@social_outreach_bp.post("/run-pass")
def run_pass():
    """Trigger an outreach pass on demand instead of waiting for the cron.

    Body: {platform: "reddit"|"self_share"|"youtube"|"recon"|"draft",
           subreddit?, keyword_profiles?, topics?, chain_draft?}
    Returns a Task-backed job id; the actual run is async on the worker.
    """
    body = request.get_json(silent=True) or {}
    platform = (body.get("platform") or "").strip().lower()
    subreddit = (body.get("subreddit") or "").strip() or None
    link_url = (body.get("link_url") or body.get("share_link") or "").strip() or None
    profiles = body.get("keyword_profiles") or body.get("topics") or None
    chain_draft = bool(body.get("chain_draft", False))

    try:
        from backend.services.social_outreach.job_service import queue_outreach_run

        queued = queue_outreach_run(
            platform,
            subreddit=subreddit,
            link_url=link_url,
            batch_size=body.get("batch_size"),
            keyword_profiles=profiles,
            chain_draft=chain_draft,
            created_by="outreach_page",
        )
        return jsonify(queued), 202
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("run_pass failed: %s", e)
        return jsonify({"error": str(e)}), 500


@social_outreach_bp.post("/record-post")
def record_post():
    """
    Called by the Discord cog / Reddit loop after a successful actual post,
    so cadence + audit row reflect the action.

    Body: {audit_id, platform, posted_text, target_url, target_thread_id}
    """
    if not kill_switch.is_enabled():
        return jsonify({"error": "outreach disabled (kill switch)"}), 403
    
    body = request.get_json(silent=True) or {}
    audit_id = body.get("audit_id")
    platform = body.get("platform")
    posted_text = body.get("posted_text", "")
    if not platform:
        return jsonify({"error": "platform required"}), 400

    # Belt-and-suspenders: tag any URL to the profile's hosts in the recorded text
    # even if the caller forgot to. The actual POSTED bytes were already
    # tagged at the servo boundary (see reddit_outreach / self_share); this
    # ensures the audit log matches what went out.
    if posted_text:
        posted_text = persona.apply_utm_tags(posted_text, platform=platform, campaign="v253")

    kill_switch.record_post(platform)

    if audit_id:
        from backend.services.social_outreach import transitions

        if not transitions.mark_posted(audit_id, posted_text):
            status = transitions.current_status(audit_id)
            if status == "rejected":
                # Posters stop at transitions.begin_submit for a rejected
                # row, so this is a poster that published without asking.
                # The rejection stands; the text that went out is kept with
                # it so the history shows both.
                logger.error(
                    "record-post: draft %s was published on %s although it is rejected",
                    audit_id, platform,
                )
                transitions.note_posted_despite_rejection(audit_id, posted_text)
                return jsonify({
                    "ok": True,
                    "status": "rejected",
                    "warning": "this draft is rejected; its status was left as rejected",
                })
    else:
        # No existing audit row to flip — this came from a code path that
        # didn't draft via /draft-comment, so log a fresh "post_recorded"
        # event so we don't lose all trace of the post. When audit_id IS
        # present, the row update above is the canonical record; logging
        # again here would double-count every successful post.
        audit.log_outreach_event(
            platform=platform,
            action="post_recorded",
            target_url=body.get("target_url"),
            target_thread_id=body.get("target_thread_id"),
            posted_text=posted_text,
            status="posted",
            task_id=body.get("task_id"),
        )

    return jsonify({"ok": True})
