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
    "browser_in_use": "The agent browser is busy with another task, so Facebook was not read.",
    "browser_unreadable": "The agent browser could not be read, so Facebook was not read.",
    "no_groups": "No Facebook group is saved and none you are in matches your outreach keywords.",
    "post_unreadable": "That Facebook post could not be read.",
    "post_takes_no_comment": "That Facebook post has no comment box for this account.",
    "no_communities": "No community was named and none is saved.",
    "no_usable_infographics": (
        "No infographic is ready to post: mark the ones to use on the Outreach page; "
        "each is usable once its text has been read back and matches."
    ),
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
        if text is None and str(reason).startswith("review_backlog:"):
            waiting = str(reason).split(":", 1)[1]
            text = (
                f"{waiting} drafts are already waiting for your review there, so no more were added. "
                "Review them first."
            )
        if text is None and str(reason).startswith("sub_bans_self_promo"):
            where = f"r/{report['subreddit']}" if report.get("subreddit") else "That community"
            text = f"{where} bans self-promotion in its rules, so it was left alone."
        if text is None and reason == "no_backed_text":
            text = "No infographic was made: the pitch sheet did not back a headline and two labels."
        if text is None and reason == "render_failed":
            text = "The infographic's text was written but the picture did not render."
        notes.append(text or f"The scout stopped: {reason}.")
        if report.get("detail"):
            notes[-1] = f"{notes[-1][:-1]} ({report['detail']})."
        return notes

    found = report.get("threads_found", report.get("comments_found"))
    candidates = int(report.get("candidates") or 0)
    if report.get("kind") == "own_posts":
        return _own_post_notes(report, candidates)
    if report.get("kind") == "made_infographic":
        return _made_infographic_notes(report)
    if isinstance(report.get("groups"), list):
        notes.extend(_group_notes(report, candidates))
    elif "comments_found" in report:
        notes.append(
            f"Read {_count(int(found), 'unanswered comment', 'unanswered comments')}; "
            f"{candidates} had no draft yet."
        )
    elif found is not None:
        notes.append(f"Read {_count(int(found), 'thread', 'threads')}; {candidates} fit.")
    skipped = []
    if report.get("skipped_dedupe"):
        skipped.append(f"{report['skipped_dedupe']} already handled")
    if report.get("skipped_answered"):
        skipped.append(f"{report['skipped_answered']} you already answered or cannot comment on")
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


def _group_notes(report: dict, candidates: int) -> list[str]:
    """What a Facebook groups scout read, group by group."""
    groups = [g for g in report["groups"] if isinstance(g, dict)]
    read = [g for g in groups if not g.get("reason")]
    notes = [
        f"Read {_count(int(report.get('posts_seen') or 0), 'post', 'posts')} in "
        f"{_count(len(read), 'Facebook group', 'Facebook groups')}; {candidates} fit."
    ]
    for group in groups:
        reason = group.get("reason")
        if not reason:
            continue
        name = group.get("name") or group.get("group") or "A group"
        if str(reason).startswith("group_bans_self_promo"):
            notes.append(f"{name} bans self-promotion in its rules, so it was left alone.")
        elif reason == "rules_unreadable":
            notes.append(f"{name}: its rules could not be read, so it was left alone.")
        else:
            notes.append(f"{name}: {reason}.")
    return notes


def _own_post_notes(report: dict, candidates: int) -> list[str]:
    """What a pass for posts of the account's own chose, community by community."""
    notes = [f"Chose an infographic for {_count(candidates, 'community', 'communities')}."]
    for entry in report.get("communities") or []:
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if not reason:
            continue
        name = entry.get("name") or entry.get("community") or "A community"
        if reason == "posted_recently":
            notes.append(f"{name}: you posted there in the last week, so it was left alone.")
        elif str(reason).startswith("bans_self_promo"):
            notes.append(f"{name} bans self-promotion in its rules, so it was left alone.")
        elif reason == "rules_unreadable":
            notes.append(f"{name}: its rules could not be read, so it was left alone.")
        elif reason == "no_unused_infographic":
            notes.append(f"{name}: every infographic you kept has already been posted there.")
        else:
            notes.append(f"{name}: {reason}.")
    return notes


def _made_infographic_notes(report: dict) -> list[str]:
    """A new infographic: what it says, what backs each line, and the read-back."""
    lines = [report.get("title") or ""] + list(report.get("callouts") or [])
    notes = [
        f"Made an infographic (file #{report.get('document_id')}): " + " / ".join(l for l in lines if l) + "."
    ]
    for line, sentence in (report.get("backing") or {}).items():
        notes.append(f"\u201c{line}\u201d rests on: {sentence}")
    if report.get("dropped"):
        notes.append("Left off because the pitch sheet did not back them: " + "; ".join(report["dropped"]) + ".")
    readback = report.get("readback") or {}
    status = readback.get("status")
    if status == "passed":
        notes.append("Read-back: every line asked for is on the picture.")
    elif status == "failed":
        missing = "; ".join(readback.get("missing") or []) or "see the Outreach page"
        notes.append(f"Read-back failed, so outreach cannot use it. Missing or misspelled: {missing}.")
    else:
        notes.append("Its text has not been read back yet.")
    notes.append("It is on the Outreach page under Infographics, All. Mark it there to let outreach use it.")
    return notes


def _turned_away(after_id: int, platform: Optional[str]) -> list[str]:
    """Drafts of this run that were written and then refused by a check, as
    lines a person can read: the words and what the check said."""
    from backend.models import SocialOutreachLog

    rows = SocialOutreachLog.query.filter(
        SocialOutreachLog.id > int(after_id), SocialOutreachLog.status == "rejected",
    )
    if platform:
        rows = rows.filter(SocialOutreachLog.platform == platform)
    lines: list[str] = []
    for row in rows.order_by(SocialOutreachLog.id.asc()).limit(10).all():
        try:
            payload = json.loads(row.draft_text or "{}")
        except ValueError:
            continue
        text = payload.get("rejected_draft") if isinstance(payload, dict) else None
        if not text:
            continue
        why = (row.abort_reason or "").strip()
        said = why[why.find("(") + 1:why.rfind(")")] if "(" in why and why.endswith(")") else why
        lines.append(f"Turned away (#{row.id}): \u201c{' '.join(text.split())}\u201d The check said: {said or 'no reason given'}")
    return lines


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
        notes.extend(_turned_away(after_id, platform))
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
