"""What one outreach request produced, for whoever started it.

A request made in chat (``/outreach reply to some posts on Reddit``) queues a
scout task and returns at once. ``run_result`` is what the chat asks afterwards:
whether the tasks have ended, what each one found in plain words, and the
drafts they wrote, ready to show for approval.
"""

from __future__ import annotations

import ast
import json
import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Task statuses after which a task writes nothing more.
ENDED = ("completed", "failed", "cancelled")
# Rows shown for a run: a draft, and what became of it since.
SHOWN_STATUSES = ("drafted", "approved", "processing", "submitting", "posted")

_REASONS = {
    "kill_switch_off": "Outreach is switched off.",
    "web_access_off": "Web access is off in Settings, so nothing was read.",
    "no_features_configured": "The outreach profile has no features to match posts against.",
    "no_targets": "No community or topic was named and none is saved.",
    "no_hot_threads": "The community's front page could not be read or was empty.",
    "no_search_results": "Reddit's search returned no threads.",
    "rules_unreadable": "The community's rules could not be read, so it was left alone.",
    "no_new_comments": "There are no unanswered comments on your videos.",
    "no_youtube_account": "No YouTube account is switched on for Outreach comments.",
    "youtube_refused": "YouTube refused the request.",
    "youtube_unreachable": "YouTube could not be reached.",
}


def presentable(row) -> dict[str, Any]:
    """A draft row as a person reads it: ``text`` is the words that would
    post, ``source`` the post or comment it answers, and ``replying_to`` the
    comment a reply answers."""
    out = row.to_dict()
    text = row.draft_text or ""
    replying_to = None
    if text.lstrip().startswith("{"):
        try:
            envelope = json.loads(text)
        except ValueError:
            envelope = None
        if isinstance(envelope, dict) and "draft" in envelope:
            text = envelope.get("draft") or ""
            if envelope.get("incoming_text"):
                replying_to = {
                    "text": envelope.get("incoming_text"),
                    "author": envelope.get("incoming_author") or "",
                }
    out["text"] = text
    out["replying_to"] = replying_to
    out["source"] = _source(row, replying_to)
    return out


def _source(row, replying_to: Optional[dict]) -> Optional[dict]:
    """The post or comment the draft answers ({title, text, author,
    community, why}); a draft written before that was kept has only the
    comment its reply envelope carries."""
    raw = getattr(row, "source_json", None)
    if raw:
        try:
            source = json.loads(raw)
        except ValueError:
            source = None
        if isinstance(source, dict):
            return source
    if replying_to:
        return {"title": "", "text": replying_to["text"], "author": replying_to["author"], "community": "", "why": ""}
    return None


def _report(task) -> Optional[dict]:
    """The dict a finished task left in ``Task.result`` (stored as its repr)."""
    raw = getattr(task, "result", None)
    if not raw:
        return None
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, dict) else None


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _scout_notes(report: dict) -> list[str]:
    notes: list[str] = []
    reason = report.get("reason")
    if reason:
        text = _REASONS.get(reason)
        if text is None and str(reason).startswith("sub_bans_self_promo"):
            where = f"r/{report['subreddit']}" if report.get("subreddit") else "That community"
            text = f"{where} bans self-promotion in its rules, so it was left alone."
        notes.append(text or f"The scout stopped: {reason}.")
        if report.get("detail"):
            notes[-1] = f"{notes[-1][:-1]} ({report['detail']})."
        return notes

    found = report.get("threads_found", report.get("comments_found"))
    candidates = int(report.get("candidates") or 0)
    if "comments_found" in report:
        notes.append(
            f"Read {_count(int(found), 'unanswered comment', 'unanswered comments')}; "
            f"{candidates} had no draft yet."
        )
    elif found is not None:
        notes.append(f"Read {_count(int(found), 'thread', 'threads')}; {candidates} fit.")
    skipped = []
    if report.get("skipped_dedupe"):
        skipped.append(f"{report['skipped_dedupe']} already handled")
    if report.get("skipped_rules"):
        skipped.append(
            f"{report['skipped_rules']} in communities that ban promotion or whose rules could not be read"
        )
    unfit = sum(int(report.get(k) or 0) for k in ("skipped_irrelevant", "skipped_by_llm", "skipped_topic"))
    if unfit:
        skipped.append(f"{unfit} not a fit for what you offer")
    if skipped:
        notes.append("Left out: " + ", ".join(skipped) + ".")
    return notes


def _task_notes(task) -> list[str]:
    if task.status == "failed":
        return [f"Task #{task.id} failed: {getattr(task, 'error_message', None) or 'no reason recorded'}."]
    if task.status == "cancelled":
        return [f"Task #{task.id} was cancelled."]
    report = _report(task)
    if report is None:
        return []
    if report.get("error"):
        return [f"Task #{task.id} failed: {report['error']}."]
    notes: list[str] = []
    scouts = report.get("reports") if isinstance(report.get("reports"), list) else [report]
    for scout in scouts:
        if isinstance(scout, dict):
            notes.extend(_scout_notes(scout))
    draft = report.get("draft")
    if isinstance(draft, dict):
        dropped = int(draft.get("rejected") or 0) + int(draft.get("errors") or 0)
        if dropped:
            notes.append(
                f"{_count(dropped, 'was', 'were')} left without a draft: nothing worth saying "
                "in reply, or the draft graded below the bar."
            )
    return notes


def run_result(
    task_ids: Iterable[int],
    after_id: int,
    platform: Optional[str] = None,
) -> dict[str, Any]:
    """Where a run stands. ``after_id`` is the newest outreach row before the
    run started; its drafts have a higher id."""
    from backend.models import SocialOutreachLog, Task

    ids = [int(t) for t in task_ids]
    tasks = Task.query.filter(Task.id.in_(ids)).order_by(Task.id.asc()).all() if ids else []
    done = all(t.status in ENDED for t in tasks)

    rows = (
        SocialOutreachLog.query
        .filter(SocialOutreachLog.id > int(after_id))
        .filter(SocialOutreachLog.status.in_(SHOWN_STATUSES))
    )
    older = SocialOutreachLog.query.filter(
        SocialOutreachLog.id <= int(after_id), SocialOutreachLog.status == "drafted",
    )
    if platform:
        rows = rows.filter(SocialOutreachLog.platform == platform)
        older = older.filter(SocialOutreachLog.platform == platform)

    notes: list[str] = []
    if done:
        for task in tasks:
            notes.extend(_task_notes(task))
    return {
        "done": done,
        "tasks": [
            {"id": t.id, "status": t.status, "progress": getattr(t, "progress", None)}
            for t in tasks
        ],
        "notes": notes,
        "drafts": [presentable(r) for r in rows.order_by(SocialOutreachLog.id.asc()).limit(25).all()],
        "older_waiting": older.count(),
    }
