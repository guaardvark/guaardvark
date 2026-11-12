"""
Content agent — Phase 2 of the multi-agent outreach pipeline.

Walks status="candidate" rows produced by Recon, asks the LLM (via the
existing persona helpers) to draft a comment that fits the thread and
the feature hint, grades the draft, and transitions the row's status:
  - candidate → drafted (grade ≥ MIN_GRADE)
  - candidate → rejected (grade < MIN_GRADE, or empty draft, or error)

Self-contained: reads everything from the candidate row's draft_text JSON
payload (which Recon populates with title, selftext_preview, top_comments,
feature_hint). No live Reddit fetch — that was Recon's job.

Doesn't post. Doesn't call the servo, and never approves: every draft it
writes waits as ``drafted`` for a person, who approves it and then starts
the post (watched_post).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from backend.services.social_outreach import audit, external_grader, gates, persona, profile

logger = logging.getLogger(__name__)


MIN_GRADE = 0.7
"""Drafts below this self-grade get rejected. Matches the grade_ok threshold
the draft-comment endpoint reports so we stay consistent."""

MIN_REPLY_GRADE = 0.6
"""Looser threshold for replies on the account's own videos — the rubric in
REPLY_TO_OWN_VIDEO_SYSTEM_BLOCK is different (we're not asking "would a
stranger flag this as promo?", we're asking "is this a real engagement?"),
so 0.6+ posts there. Set just below MIN_GRADE rather than equal to it to
make the difference explicit if someone wants to retune later."""

DEFAULT_BATCH_SIZE = 5
"""How many candidates one tick processes. Keep small — each draft is an LLM
call and we don't want a single tick blocking the worker for minutes."""


def _format_age(created_utc: float) -> str:
    """Render a thread's age as a short string ("3h", "2d") for the LLM.
    Skips the call if created_utc is missing/zero so legacy candidate rows
    written before this field existed don't spam "55 years ago"."""
    if not created_utc:
        return "unknown"
    import time
    delta = time.time() - float(created_utc)
    if delta < 0 or delta > 365 * 24 * 3600:
        return "unknown"
    hours = delta / 3600.0
    if hours < 1:
        return f"{int(delta // 60)}m"
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours // 24)}d"


def _build_thread_context(payload: dict) -> str:
    """Reconstruct the same thread_context shape that reddit_outreach.draft_via_backend
    sends to /draft-comment. Recon stored title/selftext/top_comments inline so
    Content can rebuild the context without hitting Reddit again. Subreddit and
    age are added so the drafter can match the sub's voice and frame the comment
    relative to the thread's freshness — Gemma4 specifically asked for both."""
    title = payload.get("title", "")
    selftext = payload.get("selftext_preview", "") or "(link-only post)"
    comments = payload.get("top_comments", []) or []
    subreddit = payload.get("subreddit", "")
    community = payload.get("community", "")
    header_lines = []
    if subreddit:
        header_lines.append(f"SUBREDDIT: r/{subreddit}")
    elif community:
        header_lines.append(f"COMMUNITY: {community}")
    if payload.get("created_utc") or not community:
        header_lines.append(f"THREAD AGE: {_format_age(payload.get('created_utc', 0))}")
    if payload.get("author"):
        header_lines.append(f"POSTED BY: {payload['author']}")
    header = "\n".join(header_lines)
    # A post with no title of its own (a Facebook group post) is all body.
    title_part = f"TITLE: {title}\n\n" if title else ""
    return (
        f"{header}\n\n"
        f"{title_part}"
        f"OP BODY:\n{selftext}\n\n"
        f"TOP COMMENTS:\n" + "\n---\n".join(comments[:5])
    )


def _build_reply_context(payload: dict) -> str:
    """What the grader reads for a reply on the account's own video: the video
    title, our earlier comment and the viewer's reply to it."""
    title = payload.get("title") or "(not recorded)"
    author = payload.get("incoming_author") or "a viewer"
    if not (payload.get("parent_text") or "").strip():
        return (
            f"VIDEO TITLE: {title}\n\n"
            f"THEIR COMMENT ON OUR VIDEO ({author}):\n{payload.get('incoming_text', '')}"
        )
    return (
        f"VIDEO TITLE: {title}\n\n"
        f"OUR EARLIER COMMENT:\n{payload.get('parent_text', '')}\n\n"
        f"THEIR REPLY ({author}):\n{payload.get('incoming_text', '')}"
    )


class ContentAgent:
    """Stateless drafting agent. Each call processes one candidate row.

    Returns a small dict so the caller (celery tick) can roll up a batch
    summary without re-querying the DB.
    """

    def draft_candidate(self, audit_id: int) -> dict:
        """Draft this one candidate row. Returns {status, grade, reason}.

        On any failure path the row is moved out of "candidate" status
        (either to "drafted" with the new text, or "rejected" with the
        failure reason). A row that stayed "candidate" after this call is
        a bug.
        """
        from backend.models import SocialOutreachLog
        row = SocialOutreachLog.query.get(audit_id)
        if row is None:
            return {"status": "missing", "grade": None, "reason": f"audit_id {audit_id} not found"}
        if row.status != "candidate":
            return {
                "status": "skipped",
                "grade": None,
                "reason": f"already {row.status}, not candidate",
            }

        # Defense in depth — Recon emits action="comment" / "share" / "reply".
        # An unknown action shouldn't silently be drafted; fail loud now,
        # better than a phantom row with garbage text.
        if row.action not in ("comment", "share", "reply", "post"):
            audit.mark_rejected(audit_id, f"unsupported action: {row.action!r}")
            return {"status": "rejected", "grade": None, "reason": "unsupported_action"}

        try:
            payload = json.loads(row.draft_text or "{}")
        except json.JSONDecodeError:
            audit.mark_rejected(audit_id, "draft_text JSON unparseable (legacy or corrupt row)")
            return {"status": "rejected", "grade": None, "reason": "json_decode_error"}

        feature_hint = payload.get("feature_hint")

        try:
            if row.action == "reply":
                # Reply path — different persona, no thread-context format,
                # no feature_hint. Recon stashed parent_text + incoming_text
                # in the candidate payload.
                result = persona.draft_outreach_text(
                    platform=row.platform,
                    context={
                        "parent_text": payload.get("parent_text", ""),
                        "incoming_text": payload.get("incoming_text", ""),
                        "incoming_author": payload.get("incoming_author", ""),
                        "video_title": payload.get("title", ""),
                    },
                    mode="reply",
                )
            elif row.action == "post":
                result = persona.draft_outreach_text(
                    platform=row.platform,
                    context={
                        "community": payload.get("community") or row.target_url or "",
                        "infographic": payload.get("infographic") or {},
                    },
                    mode="post",
                )
            elif row.action == "share":
                result = persona.draft_outreach_text(
                    platform=row.platform,
                    context={
                        "target": payload.get("target")
                        or payload.get("share_target")
                        or row.target_url
                        or "(unspecified)",
                        "link_url": payload.get("link_url")
                        or payload.get("share_link")
                        or profile.site_url(),
                    },
                    mode="share",
                    feature_hint=feature_hint,
                )
            else:
                thread_context = _build_thread_context(payload)
                # YouTube outbound comments carry the profile's repo (or site)
                # link on relevant videos. Reddit comments stay soft-mention
                # unless a caller opts in.
                is_youtube = (row.platform or "").strip().lower() == "youtube"
                result = persona.draft_outreach_text(
                    platform=row.platform,
                    context={"thread_context": thread_context, "url": row.target_url},
                    mode="comment",
                    feature_hint=feature_hint,
                    include_link=is_youtube,
                    link_url=profile.cta_url("youtube") if is_youtube else None,
                )
        except Exception as e:
            logger.warning("ContentAgent.draft_candidate %s: persona call raised: %s", audit_id, e)
            audit.mark_rejected(audit_id, f"draft_call_failed: {e}")
            return {"status": "rejected", "grade": None, "reason": "draft_call_failed"}

        draft_text = (result.get("draft") or "").strip()
        grade = float(result.get("grade") or 0.0)

        if not draft_text:
            # The persona says why when it made no draft at all (nothing to
            # promote, no link to give); otherwise the model declined.
            why = (result.get("reason") or "").strip() if grade == 0.0 else ""
            audit.mark_rejected(audit_id, f"empty draft: {why}" if why else "empty draft from LLM")
            return {"status": "rejected", "grade": grade, "reason": "empty_draft"}

        grade_threshold = MIN_REPLY_GRADE if row.action == "reply" else MIN_GRADE
        if grade < grade_threshold:
            audit.mark_rejected(audit_id, f"grade_too_low:{grade:.2f}", draft=draft_text)
            return {"status": "rejected", "grade": grade, "reason": "grade_too_low"}

        # A share has no thread for the rubric at all. A reply on the
        # account's own video answers one viewer, so it is asked only whether
        # it answers them and whether the tone fits (the "reply" rubric), not
        # whether it engages a thread.
        if row.action in ("share", "post"):
            ext = {"checked": False, "skipped": True, "reason": "share_not_graded"}
        elif row.action == "reply":
            ext = external_grader.grade_draft_externally(
                draft_text, _build_reply_context(payload), kind="reply",
            )
        else:
            # Second-opinion grade — different model family, rubric-based,
            # blind to the self-grade. Drafter is biased toward its own
            # output; this catches generic, off-tone, or oversold comments
            # that the writer rated highly. If the grader is unavailable the
            # draft is not rejected; the person reviewing it sees that.
            ext = external_grader.grade_draft_externally(draft_text, thread_context)
        if gates.independent_check_label(ext) == "failed":
            asked = ext.get("questions") or external_grader.RUBRIC_QUESTIONS
            missed = ",".join(q for q in asked if not ext.get(q))
            reason = f"external_check_failed:{missed or 'not_passed'} ({ext.get('reason', '')[:120]})"
            audit.mark_rejected(audit_id, reason, draft=draft_text)
            return {
                "status": "rejected",
                "grade": grade,
                "reason": "external_check_failed",
                "external": ext,
            }

        # UTM-tag any links to the profile's own hosts, same as the
        # existing /draft-comment endpoint does. Tagging at the draft
        # boundary catches every URL — including ones the user may later
        # edit into the draft via the UI. (For replies this is a no-op
        # since replies don't carry links.)
        posted_text = persona.apply_utm_tags(
            draft_text, platform=row.platform, campaign="v253",
        )

        # Replies need their parent-comment anchor preserved through the
        # candidate→drafted transition because mark_drafted_from_candidate
        # overwrites draft_text and there's no extras column to carry the
        # anchor in. Encode draft_text as a JSON envelope for replies;
        # the dispatcher parses it back out. posted_text stays plain
        # (it's what gets typed into the composer) so the existing
        # `row.posted_text or row.draft_text` pattern keeps working for
        # callers that don't care about the envelope.
        stored_draft = draft_text
        if row.action == "reply":
            anchor = (
                payload.get("anchor_hint")
                or payload.get("parent_text", "")[:200]
                or ""
            )
            stored_draft = json.dumps({
                "draft": draft_text,
                "anchor": anchor,
                # Stash the incoming reply too, so the supervised-approval
                # UI can show "you're replying to this" without rejoining
                # to the recon-stage jsonl.
                "incoming_text": payload.get("incoming_text", ""),
                "incoming_author": payload.get("incoming_author", ""),
            })

        # Store posted_text alongside the draft so the poster doesn't have to
        # re-tag at servo time. Goes through the audit helper (detached
        # session) so we don't accidentally flush other caller-pending
        # mutations under celery. The draft waits for a person whatever the
        # grades say; a thread recon could not put to the thread-fit judge is
        # noted for them (relevance_skipped in the candidate payload).
        relevance_unchecked = bool(payload.get("relevance_skipped"))
        independent_pass, independent_reason = gates.independent_ok(
            ext, action=row.action, relevance_unchecked=relevance_unchecked,
        )
        promote_status = "drafted"
        hold_reason = None if independent_pass else independent_reason

        promoted = audit.mark_drafted_from_candidate(
            audit_id,
            draft_text=stored_draft,
            grade_score=grade,
            posted_text=posted_text,
            status=promote_status,
        )
        if not promoted:
            # Race: someone else moved it out of "candidate" between fetch
            # and update. Re-checking the current state would be a best-effort
            # second hop; for now just report the skip.
            return {"status": "skipped", "grade": grade, "reason": "race_lost_during_promotion"}

        # Trail recon-stage signals into the audit jsonl so they survive past
        # the candidate→drafted column overwrite. jsonl-only (no new DB row),
        # queryable from disk for analytics: which feature_hints get drafted vs
        # rejected, which subs convert best, etc.
        audit.log_trail_only(
            platform=row.platform,
            event="candidate_promoted",
            target_url=row.target_url,
            target_thread_id=row.target_thread_id,
            extra={
                "audit_id": audit_id,
                "feature_hint": feature_hint,
                "title": payload.get("title"),
                "subreddit": payload.get("subreddit"),
                "self_grade": grade,
                "external_grade": ext.get("grade"),
                "external_passed": ext.get("passed"),
                "external_checked": bool(ext.get("checked")),
                "external_skipped": ext.get("skipped", False),
                "external_reason": ext.get("reason", ""),
                "external_model": ext.get("model"),
                "drafter_model": ext.get("drafter_model"),
                "external_independent": ext.get("independent"),
                "relevance_unchecked": relevance_unchecked,
                "independent_reason": independent_reason,
                "promoted_status": promote_status,
                "hold_reason": hold_reason,
            },
        )

        return {
            "status": promote_status,
            "grade": grade,
            "reason": None,
            "external": ext,
            "independent_reason": independent_reason,
            "hold_reason": hold_reason,
        }

    def draft_batch(self, batch_size: int = DEFAULT_BATCH_SIZE, after_id: Optional[int] = None) -> dict:
        """Walk the oldest N candidate rows and draft each. Returns a summary.

        ``after_id`` limits the walk to rows with a higher id: the candidates
        one scout pass just added, when a person's request chains the draft
        step onto it.

        Stops at batch_size to keep individual ticks bounded — a celery beat
        every few minutes will drain the queue eventually.

        A row whose drafting raises is rejected as ``draft_crashed`` and
        counted under errors, and the rows after it are still drafted. Left
        as a candidate it would head the oldest-first query again and stop
        every later tick at the same row.
        """
        from backend.models import SocialOutreachLog, db
        from backend.services.social_outreach import transitions
        candidates = SocialOutreachLog.query.filter(SocialOutreachLog.status == "candidate")
        if after_id is not None:
            candidates = candidates.filter(SocialOutreachLog.id > after_id)
        row_ids = [
            row.id for row in (
                candidates
                .order_by(SocialOutreachLog.created_at.asc())
                .limit(batch_size)
                .all()
            )
        ]
        report = {
            "considered": len(row_ids),
            "drafted": 0,
            "approved": 0,
            "rejected": 0,
            "errors": 0,
        }
        for row_id in row_ids:
            try:
                outcome = self.draft_candidate(row_id)
            except Exception as e:
                logger.exception("ContentAgent.draft_batch: drafting %s raised", row_id)
                report["errors"] += 1
                db.session.rollback()
                # Only a row still waiting as a candidate is rejected; one the
                # crash caught after promotion keeps its draft.
                if not transitions.move(
                    row_id, "rejected", ("candidate",),
                    abort_reason=f"draft_crashed: {type(e).__name__}: {e}"[:512],
                ):
                    logger.warning(
                        "ContentAgent.draft_batch: %s was no longer a candidate after the crash; left as is",
                        row_id,
                    )
                continue
            status = outcome["status"]
            if status == "drafted":
                report["drafted"] += 1
            elif status == "approved":
                report["approved"] += 1
            elif status == "rejected":
                report["rejected"] += 1
            else:
                report["errors"] += 1
        return report
