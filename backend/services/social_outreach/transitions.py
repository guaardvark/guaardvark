"""Status transitions for social outreach draft rows.

    candidate -> drafted -> approved -> processing -> submitting -> posted
                    |           |            |              |
                    |           |            +--> aborted <-+
                    +-----------+--> unsupported
    rejected <- (candidate, drafted, approved, processing)

``unsupported`` means nothing in this install posts that action on that
platform (a reply anywhere but YouTube, for one, or any draft for a platform
``platforms.REFUSED`` lists); the row keeps its draft and is not retried.

``processing`` means a poster has claimed the row and is driving the browser
towards the composer; the row can still be rejected. ``submitting`` means the
poster has started the step that publishes, which cannot be taken back.

Every change of status goes through ``move``, a compare-and-set in one UPDATE,
so a reject and a poster racing for the same row cannot both win: either the
reject lands and ``begin_submit`` refuses, or the submit has begun and the
reject is refused.

While public posting is stopped (``kill_switch.posting_stop_reason``),
``claim`` and ``begin_submit`` refuse and an approved draft stays approved, so
it waits for the stop to lift rather than being abandoned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, NamedTuple, Optional

from sqlalchemy import func

# Every status a row can hold, in pipeline order.
KNOWN_STATUSES = (
    "candidate",
    "drafted",
    "approved",
    "processing",
    "submitting",
    "posted",
    "rejected",
    "aborted",
    "unsupported",
)

# Approve only from drafted, by a person.
APPROVE_FROM = frozenset({"drafted"})

# Reject cancels work whose publishing step has not started.
REJECT_FROM = frozenset({"candidate", "drafted", "approved", "processing"})

# Claim for posting (Celery tick / Discord cog).
CLAIM_FROM = frozenset({"approved"})

# Start the publishing step. Only a claimed row that nobody rejected.
SUBMIT_FROM = frozenset({"processing"})

# A poster giving up on a row it claimed.
ABORT_FROM = frozenset({"processing", "submitting"})

# An approved row no poster handles leaves the queue before it is claimed.
UNSUPPORTED_FROM = frozenset({"approved"})

# The reason a poster returns when ``begin_submit`` refused, so its caller can
# tell a withdrawn row from a servo failure.
WITHDRAWN_BEFORE_SUBMIT = "withdrawn_before_submit"

# abort_reason doubles as the claim timestamp while a row is in flight; the
# table has no updated_at column for the stuck-row reaper to age rows by.
_IN_FLIGHT_STAMPS = ("processing_since:", "submitting_since:")


def can_approve(status: str | None) -> bool:
    return (status or "") in APPROVE_FROM


def can_reject(status: str | None) -> bool:
    return (status or "") in REJECT_FROM


def can_claim(status: str | None) -> bool:
    return (status or "") in CLAIM_FROM


def reject_refusal(status: str | None) -> str:
    """Why a row in ``status`` cannot be rejected, in words a user can act on."""
    base = f"cannot reject from status '{status}'"
    if status == "submitting":
        return base + ": the post is being submitted right now and can no longer be stopped"
    if status == "posted":
        return base + ": it is already posted, and rejecting it would not take the post down"
    if status == "rejected":
        return base + ": it is already rejected and will not post"
    if status == "aborted":
        return base + ": posting it was abandoned and it will not be retried"
    if status == "unsupported":
        return base + ": nothing here can post that action on that platform, so it will not post"
    return base


def _stamp(prefix: str) -> str:
    return f"{prefix}{datetime.now(timezone.utc).isoformat()}"


def in_flight_since(abort_reason: str | None) -> Optional[datetime]:
    """When a processing/submitting row entered that status, or None."""
    reason = abort_reason or ""
    for prefix in _IN_FLIGHT_STAMPS:
        if reason.startswith(prefix):
            try:
                stamp = datetime.fromisoformat(reason[len(prefix):].replace("Z", "+00:00"))
            except ValueError:
                return None
            return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    return None


def move(audit_id: int, to_status: str, allowed_from: Iterable[str], **fields: Any) -> bool:
    """Set ``status`` (and ``fields``) only if the row's status is still one of
    ``allowed_from``. True when this call made the change.

    Commits ``db.session``; ORM objects the caller holds are refreshed on their
    next read.
    """
    from backend.models import SocialOutreachLog

    return _update_where(
        audit_id, SocialOutreachLog.status.in_(tuple(allowed_from)), {"status": to_status, **fields},
    )


def _update_where(audit_id: int, status_is, values: dict) -> bool:
    from backend.models import SocialOutreachLog, db

    try:
        changed = (
            db.session.query(SocialOutreachLog)
            .filter(SocialOutreachLog.id == audit_id)
            .filter(status_is)
            .update(values, synchronize_session=False)
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return changed == 1


def current_status(audit_id: int) -> Optional[str]:
    """The row's status as the database has it now, or None if there is no row."""
    from backend.models import SocialOutreachLog, db

    found = (
        db.session.query(SocialOutreachLog.status)
        .filter(SocialOutreachLog.id == audit_id)
        .first()
    )
    return found[0] if found else None


def _platform(audit_id: int) -> Optional[str]:
    from backend.models import SocialOutreachLog, db

    found = (
        db.session.query(SocialOutreachLog.platform)
        .filter(SocialOutreachLog.id == audit_id)
        .first()
    )
    return found[0] if found else None


def approve(audit_id: int, draft_text: Optional[str] = None) -> bool:
    """drafted -> approved. A draft for a platform nothing here posts to
    (``platforms.REFUSED``) goes to unsupported with that platform's reason
    instead, and False is returned; ``approval_refusal`` then says why."""
    from backend.models import SocialOutreachLog
    from backend.services.social_outreach import platforms

    refusal = platforms.refusal_for(_platform(audit_id))
    if refusal:
        move(audit_id, "unsupported", APPROVE_FROM, abort_reason=refusal[:512])
        return False
    fields = {} if draft_text is None else edited_fields(audit_id, draft_text)
    # The platform is matched in the same UPDATE, so a draft switched to a
    # refused platform after the read above is not approved either.
    refused = platforms.platforms_in(platforms.REFUSED)
    return _update_where(
        audit_id,
        SocialOutreachLog.status.in_(tuple(APPROVE_FROM))
        & func.lower(func.coalesce(SocialOutreachLog.platform, "")).not_in(refused),
        {"status": "approved", **fields},
    )


def edited_fields(audit_id: int, text: str) -> dict:
    """The column values that make ``text`` the text that posts for this row.

    The posters send ``posted_text`` when a row has one (the Content agent
    stores its tagged copy there), so an edit clears it and they build the
    post from the edited draft. A reply's draft is a JSON envelope that also
    names the comment it answers; the edit replaces the envelope's text and
    keeps the rest. The edited text has not been graded, so the grade goes.
    """
    import json

    from backend.models import SocialOutreachLog, db

    current = (
        db.session.query(SocialOutreachLog.draft_text)
        .filter(SocialOutreachLog.id == audit_id)
        .scalar()
    )
    def envelope_of(value: Optional[str]) -> Optional[dict]:
        if not value or not value.lstrip().startswith("{"):
            return None
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) and "draft" in parsed else None

    merged = text
    envelope = envelope_of(current)
    # A caller that sends back a whole envelope has edited it as one.
    if envelope is not None and envelope_of(text) is None:
        merged = json.dumps({**envelope, "draft": text})
    fields = {"draft_text": merged, "posted_text": None}
    before = envelope.get("draft") if envelope is not None else current
    if (before or "").strip() != (envelope_of(merged) or {"draft": merged})["draft"].strip():
        # The grade was given to the text this replaces.
        fields["grade_score"] = None
    return fields


def approval_refusal(audit_id: int) -> Optional[str]:
    """Why ``approve`` did not approve this row, in words a user can act on;
    None when there is no such row."""
    from backend.models import SocialOutreachLog, db

    found = (
        db.session.query(SocialOutreachLog.status, SocialOutreachLog.abort_reason)
        .filter(SocialOutreachLog.id == audit_id)
        .first()
    )
    if not found:
        return None
    status, reason = found
    if status == "unsupported" and reason:
        return reason
    return f"cannot approve from status '{status}' (only from drafted)"


def _posting_stopped() -> bool:
    from backend.services.social_outreach import kill_switch

    return kill_switch.posting_stopped()


def claim(audit_id: int) -> bool:
    """approved -> processing. False when the row is no longer approved, or
    while public posting is stopped (the row then stays approved)."""
    if _posting_stopped():
        return False
    return move(audit_id, "processing", CLAIM_FROM, abort_reason=_stamp("processing_since:"))


def release_claim(audit_id: int) -> bool:
    """processing -> approved: a poster hands back a row it claimed and did
    not submit, so the draft waits instead of being abandoned. Its give-up
    afterwards finds nothing in flight."""
    return move(audit_id, "approved", SUBMIT_FROM, abort_reason=None)


def begin_submit(audit_id: int) -> bool:
    """processing -> submitting. A poster calls this immediately before the
    step that publishes and must not publish when it returns False: the row was
    rejected (or reaped) while the poster was working, or public posting was
    stopped. A stop hands the claim back (``release_claim``), so the draft
    waits."""
    if _posting_stopped():
        release_claim(audit_id)
        return False
    return move(audit_id, "submitting", SUBMIT_FROM, abort_reason=_stamp("submitting_since:"))


def submit_gate(audit_id: int) -> Callable[[], bool]:
    """``begin_submit`` for one row, in the shape posters take as ``before_submit``."""
    return lambda: begin_submit(audit_id)


def abort_in_flight(audit_id: int, reason: str) -> bool:
    """processing/submitting -> aborted. Leaves a row that was rejected meanwhile alone."""
    return move(audit_id, "aborted", ABORT_FROM, abort_reason=(reason or "")[:512])


def mark_unsupported(audit_id: int, reason: str) -> bool:
    """approved -> unsupported, for a row no poster handles. Leaves a row that
    was claimed or rejected meanwhile alone."""
    return move(audit_id, "unsupported", UNSUPPORTED_FROM, abort_reason=(reason or "")[:512])


def mark_posted(audit_id: int, posted_text: str) -> bool:
    """Record a published post. False when the row is missing or rejected.

    A post that went out is recorded whatever the row said, except over a
    rejection: a rejected row never turns into a posted one.
    """
    from backend.models import SocialOutreachLog

    return _update_where(
        audit_id, SocialOutreachLog.status != "rejected",
        {"status": "posted", "posted_text": posted_text},
    )


def note_posted_despite_rejection(audit_id: int, posted_text: str) -> bool:
    """Keep what was published on a rejected row without changing its status."""
    return move(
        audit_id, "rejected", ("rejected",),
        posted_text=posted_text, abort_reason="posted_despite_rejection",
    )


class RejectOutcome(NamedTuple):
    rejected: bool
    # The status the row was rejected from; when not rejected, the status that
    # blocked it, or None if the row does not exist.
    status: Optional[str]


def reject(audit_id: int) -> RejectOutcome:
    """Reject a row unless its publishing step has started or it is finished."""
    # A row can change status between the read and the UPDATE (the poster
    # claiming it, for one), so the UPDATE names the status it read and the
    # loop re-reads when that no longer matches.
    status = current_status(audit_id)
    for _ in range(len(KNOWN_STATUSES)):
        if status is None or not can_reject(status):
            return RejectOutcome(False, status)
        # A claimed row carries its claim time in abort_reason; replace it so
        # the history says why a claimed draft never posted.
        fields = {"abort_reason": "rejected_before_submit"} if status == "processing" else {}
        if move(audit_id, "rejected", (status,), **fields):
            return RejectOutcome(True, status)
        status = current_status(audit_id)
    return RejectOutcome(False, status)
