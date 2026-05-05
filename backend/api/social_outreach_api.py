"""
HTTP surface for the social outreach loop.

Endpoints
---------
GET  /api/social-outreach/status              — enabled/supervised/cadence snapshot
POST /api/social-outreach/enable              — flip global on
POST /api/social-outreach/kill                — flip global off (hard stop)
POST /api/social-outreach/supervised          — body {"on": bool}
GET  /api/social-outreach/audit?limit=200     — recent log rows
GET  /api/social-outreach/queue               — drafted-but-not-posted entries (supervised mode)
POST /api/social-outreach/drafts              — create a draft manually from the UI
PATCH /api/social-outreach/drafts/<id>        — save edits to a draft (status='drafted' only)
POST /api/social-outreach/approve/<id>        — approve a queued draft (will be posted on next pass)
POST /api/social-outreach/reject/<id>         — reject and mark won't-post
POST /api/social-outreach/draft-comment       — internal: draft a reply via the LLM, with grade
POST /api/social-outreach/scout-url           — agent fetches OP + comments for a URL so the human doesn't paste them
POST /api/social-outreach/run-pass            — fire a Reddit / self-share pass on demand instead of waiting for the cron

The draft-comment endpoint is the LLM call site for both the Discord cog and
the Reddit browser loop. It uses Ollama directly with the persona block — no
chat session, no streaming, no tools. One-shot completion that returns
{draft, grade, reason}.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from backend.services.social_outreach import audit, kill_switch, persona
from backend.services.social_outreach.persona import (
    FEATURE_BLURBS,
    GITHUB_URL,
    GUAARDVARK_PITCH,
    OUTWARD_FACING_SYSTEM_BLOCK,
    SHARE_FRAMING,
    SITE_URL,
    GOTHAM_RISING_URL,
    TONE_GUIDES,
    find_relevant_feature,
)

logger = logging.getLogger(__name__)

social_outreach_bp = Blueprint("social_outreach", __name__, url_prefix="/api/social-outreach")


# --- Status / control ----------------------------------------------------

@social_outreach_bp.get("/status")
def status():
    return jsonify({
        "enabled": kill_switch.is_enabled(),
        "supervised": kill_switch.is_supervised(),
        "caps": {
            "min_gap_seconds": kill_switch.CADENCE_MIN_GAP_SECONDS,
            "daily_cap": kill_switch.CADENCE_DAILY_CAP,
            "servo_failure_abort_threshold": kill_switch.SERVO_FAILURE_ABORT_THRESHOLD,
        },
        "cadence": kill_switch.cadence_status(),
    })


@social_outreach_bp.post("/enable")
def enable():
    kill_switch.set_enabled(True)
    return jsonify({"enabled": True})


@social_outreach_bp.post("/kill")
def kill():
    kill_switch.set_enabled(False)
    return jsonify({"enabled": False})


@social_outreach_bp.post("/supervised")
def set_supervised():
    body = request.get_json(silent=True) or {}
    on = bool(body.get("on", True))
    kill_switch.set_supervised(on)
    return jsonify({"supervised": on})


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
    """Drafts that are pending approval (supervised mode produces these)."""
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
    """Drafts that have been approved and are waiting to be posted."""
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

    Body: {platform, action?, target_url?, target_thread_id?, draft_text?, grade_score?}
    Returns the new SocialOutreachLog row.
    """
    body = request.get_json(silent=True) or {}
    platform = (body.get("platform") or "").strip()
    if not platform:
        return jsonify({"error": "platform required"}), 400
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
    # way an LLM-drafted row does. Marks "source=manual_ui" so we can later
    # tell hand-rolled drafts apart from the cron-fed ones.
    audit_id = audit.log_outreach_event(
        platform=platform,
        action=action,
        target_url=target_url,
        target_thread_id=target_thread_id,
        draft_text=draft_text,
        status="drafted",
        grade_score=grade_score,
        extra={"source": "manual_ui"},
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
    from backend.models import SocialOutreachLog, db
    row = SocialOutreachLog.query.get(event_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    
    body = request.get_json(silent=True) or {}
    if "draft_text" in body:
        row.draft_text = body["draft_text"]
        
    row.status = "approved"
    db.session.commit()
    return jsonify(row.to_dict())


@social_outreach_bp.post("/reject/<int:event_id>")
def reject(event_id: int):
    from backend.models import SocialOutreachLog, db
    row = SocialOutreachLog.query.get(event_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    row.status = "rejected"
    db.session.commit()
    return jsonify(row.to_dict())


# --- Draft-comment endpoint (the LLM call site) --------------------------

def _ollama_json_chat(system: str, user: str, model: Optional[str] = None) -> Dict[str, Any]:
    """One-shot Ollama call that returns parsed JSON. Best-effort.

    Forces format=json so the model emits valid JSON; we still try/except
    around the parse because models occasionally truncate or wrap.
    """
    import ollama

    if model is None:
        from backend.config import get_default_llm
        model = get_default_llm()

    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format="json",
            options={"temperature": 0.6},
        )
    except Exception as e:
        logger.error("ollama.chat failed: %s", e)
        return {"draft": "", "grade": 0.0, "reason": f"LLM unavailable: {e}"}

    # ollama-python ≥0.2 returns a typed ChatResponse (Pydantic), not a dict.
    # Older versions returned a plain dict. Support both: prefer attribute access
    # when available, fall back to dict access for legacy SDKs.
    msg = getattr(response, "message", None)
    if msg is None and isinstance(response, dict):
        msg = response.get("message")
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content", "")
    raw = (content or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # last-ditch — pull the first {...} block
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning("ollama returned non-JSON: %s", raw[:200])
        return {"draft": "", "grade": 0.0, "reason": "model returned malformed JSON"}


def _build_user_prompt(
    platform: str,
    thread_context: str,
    target_url: Optional[str],
    feature_hint: Optional[str],
    tone: Optional[str] = None,
) -> str:
    """Compose the user-side prompt for the LLM. Keeps the system block clean."""
    feature = feature_hint or find_relevant_feature(thread_context) or "local_ai"
    feature_blurb = FEATURE_BLURBS.get(feature, FEATURE_BLURBS["local_ai"])
    tone_guide = TONE_GUIDES.get((tone or "default").lower(), "").strip()
    tone_block = f"\nTONE OVERRIDE: {tone_guide}\n" if tone_guide else ""

    return f"""\
PLATFORM: {platform}
TARGET URL: {target_url or "(unknown)"}

THREAD CONTEXT:
\"\"\"
{thread_context.strip()[:4000]}
\"\"\"

GUAARDVARK PITCH (one line, only paraphrase if needed):
{persona.GUAARDVARK_PITCH}

RELEVANT FEATURE TO MENTION (only if it actually fits):
{feature}: {feature_blurb}

LINK TO INCLUDE (only if natural — don't force it):
{SITE_URL}
{tone_block}
Write a comment that adds value to this thread first. If a Guaardvark mention fits, drop it as a casual reference, not a pitch. If nothing fits, return draft="" and grade < 0.3.

Respond with JSON: {{"draft": "...", "grade": 0.0-1.0, "reason": "..."}}.
"""


def _build_share_prompt(platform: str, target: str, link_url: str) -> str:
    framing = SHARE_FRAMING.get(platform, SHARE_FRAMING["reddit"])
    return f"""\
PLATFORM: {platform}
TARGET COMMUNITY: {target}
LINK: {link_url}

GUAARDVARK PITCH:
{persona.GUAARDVARK_PITCH}

INSTRUCTIONS:
{framing}

Respond with JSON: {{"title": "...", "body": "...", "grade": 0.0-1.0, "reason": "..."}} for reddit, or {{"draft": "...", "grade": 0.0-1.0, "reason": "..."}} for other platforms.
"""


@social_outreach_bp.post("/draft-comment")
def draft_comment():
    """
    Body: {
        "platform": "reddit"|"discord"|"facebook",
        "thread_context": "OP + top comments concatenated",
        "target_url": "https://...",
        "target_thread_id": "abc123",  # optional, for dedupe
        "feature_hint": "video_gen",   # optional, override auto-detect
        "task_id": 42,                 # optional, links audit row to celery task
        "mode": "comment"|"share",     # default "comment"
        "share_target": "r/SideProject",  # required for share mode
        "share_link": "https://guaardvark.com",  # required for share mode
    }
    Returns: {draft, grade, reason, audit_id, would_post}
    """
    body = request.get_json(silent=True) or {}
    platform = body.get("platform", "unknown")
    mode = body.get("mode", "comment")
    task_id = body.get("task_id")
    target_url = body.get("target_url")
    target_thread_id = body.get("target_thread_id")

    if mode == "share":
        target = body.get("share_target") or "(unspecified)"
        link_url = body.get("share_link") or SITE_URL
        prompt = _build_share_prompt(platform, target, link_url)
        result = _ollama_json_chat(OUTWARD_FACING_SYSTEM_BLOCK, prompt)
        # Reddit returns title+body; collapse for storage. We also stash the
        # link_url here so an approved share posted hours later uses the URL
        # the user actually drafted against, not whatever SITE_URL happens to
        # be at post time.
        if platform == "reddit":
            draft_text = json.dumps({
                "title": result.get("title", ""),
                "body": result.get("body", ""),
                "link_url": link_url,
            })
        else:
            draft_text = result.get("draft", "")
        grade = float(result.get("grade", 0.0) or 0.0)
        reason = result.get("reason", "")
    else:
        thread_context = body.get("thread_context", "")
        if not thread_context:
            return jsonify({"error": "thread_context required"}), 400
        prompt = _build_user_prompt(
            platform=platform,
            thread_context=thread_context,
            target_url=target_url,
            feature_hint=body.get("feature_hint"),
            tone=body.get("tone"),
        )
        result = _ollama_json_chat(OUTWARD_FACING_SYSTEM_BLOCK, prompt)
        draft_text = result.get("draft", "")
        grade = float(result.get("grade", 0.0) or 0.0)
        reason = result.get("reason", "")

    enabled = kill_switch.is_enabled()
    supervised = kill_switch.is_supervised()
    cadence_ok, cadence_reason = kill_switch.cadence_allows_post(platform)
    grade_ok = grade >= 0.7
    has_draft = bool(draft_text.strip())

    would_post = enabled and not supervised and cadence_ok and grade_ok and has_draft

    audit_id = audit.log_outreach_event(
        platform=platform,
        action="comment" if mode == "comment" else "share",
        target_url=target_url,
        target_thread_id=target_thread_id,
        draft_text=draft_text,
        posted_text=None,
        status="drafted",
        grade_score=grade,
        abort_reason=None,
        task_id=task_id,
        extra={"reason": reason, "would_post": would_post, "cadence_block": cadence_reason if not cadence_ok else None},
    )

    return jsonify({
        "audit_id": audit_id,
        "draft": draft_text,
        "grade": grade,
        "reason": reason,
        "would_post": would_post,
        "gates": {
            "enabled": enabled,
            "supervised": supervised,
            "cadence_ok": cadence_ok,
            "cadence_reason": cadence_reason,
            "grade_ok": grade_ok,
            "has_draft": has_draft,
        },
    })


# --- Snippet bank + citation helper (powering the OutreachPage widgets) ---

@social_outreach_bp.get("/snippets")
def get_snippets():
    """Pre-built copy blocks for the snippet bank in the Drafting zone.
    Read-only — these are the canonical Guaardvark pitches."""
    return jsonify({
        "pitch": GUAARDVARK_PITCH,
        "site_url": SITE_URL,
        "github_url": GITHUB_URL,
        "gotham_rising_url": GOTHAM_RISING_URL,
        "feature_blurbs": FEATURE_BLURBS,
        "tones": list(TONE_GUIDES.keys()),
    })


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
    host = (host or "").lower()
    if "reddit.com" in host:
        return "reddit"
    if "discord.com" in host or "discord.gg" in host:
        return "discord"
    if "facebook.com" in host or "fb.com" in host:
        return "facebook"
    if "twitter.com" in host or "x.com" in host:
        return "twitter"
    return None


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
    base = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    json_url = base + ".json?limit=10&depth=1"
    headers = {"User-Agent": "guaardvark-outreach/0.1 scout"}
    try:
        resp = requests.get(json_url, headers=headers, timeout=10, allow_redirects=True)
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
        headers = {"User-Agent": "guaardvark-outreach/0.1 scout"}
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
        html = resp.text[:200_000]
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
      discord/twitter/facebook → drive the agent's logged-in Firefox via BiDi
      anything else → SSRF-guarded HTML scrape (the original cheap path)
    """
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    force_dom = bool(body.get("force_dom"))
    if not url or not url.startswith(("http://", "https://")):
        return jsonify({"error": "url must include http(s)://"}), 400

    parsed_host = ""
    try:
        from urllib.parse import urlparse
        parsed_host = (urlparse(url).hostname or "").lower()
    except Exception:
        pass

    # Reddit JSON API — primary path unless caller forces DOM.
    if "reddit.com" in parsed_host and not force_dom:
        scouted = _scout_reddit_url(url)
        if scouted:
            return jsonify(scouted)
        # JSON failed (rate-limited, verification wall, etc) — fall through to DOM.

    # Drive Firefox via BiDi for the platforms whose pages are JS shells. The
    # agent's session inherits the user's logged-in cookies on display restart,
    # so this works for Discord/Twitter/Facebook without any auth dance.
    from backend.services.dom_metadata_extractor import DOMMetadataExtractor
    extractor = DOMMetadataExtractor.get_instance()
    platform = extractor.detect_platform(url)
    if platform in ("discord", "twitter", "facebook", "reddit"):
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


@social_outreach_bp.post("/run-pass")
def run_pass():
    """Trigger an outreach pass on demand instead of waiting for the cron.

    Body: {platform: "reddit"|"self_share", subreddit?: "SideProject"}
    Returns the Celery task id; the actual run is async on the worker.
    """
    body = request.get_json(silent=True) or {}
    platform = (body.get("platform") or "").strip().lower()
    subreddit = (body.get("subreddit") or "").strip() or None

    if not kill_switch.is_enabled():
        return jsonify({"error": "outreach is disabled (kill switch is off)"}), 400

    try:
        if platform == "reddit":
            from backend.tasks.social_outreach_tasks import (
                engage_with_subreddit,
                tick_reddit_outreach,
            )
            async_result = (
                engage_with_subreddit.delay(subreddit) if subreddit
                else tick_reddit_outreach.delay()
            )
            return jsonify({
                "task_id": async_result.id,
                "platform": "reddit",
                "subreddit": subreddit,
                "message": (
                    f"Reddit pass queued for r/{subreddit}." if subreddit
                    else "Reddit pass queued (round-robin pick from targets.json)."
                ),
            })
        if platform == "self_share":
            from backend.tasks.social_outreach_tasks import tick_self_share
            async_result = tick_self_share.delay()
            return jsonify({
                "task_id": async_result.id,
                "platform": "self_share",
                "message": "Self-share pass queued (link post to next round-robin sub).",
            })
        return jsonify({
            "error": f"unsupported platform '{platform}'. Use 'reddit' or 'self_share'.",
        }), 400
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
    body = request.get_json(silent=True) or {}
    audit_id = body.get("audit_id")
    platform = body.get("platform")
    posted_text = body.get("posted_text", "")
    if not platform:
        return jsonify({"error": "platform required"}), 400

    kill_switch.record_post(platform)

    if audit_id:
        from backend.models import SocialOutreachLog, db
        row = SocialOutreachLog.query.get(audit_id)
        if row is not None:
            row.status = "posted"
            row.posted_text = posted_text
            db.session.commit()
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
