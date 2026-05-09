"""
Recon agent — Phase 1 of the multi-agent outreach pipeline.

Job: scan platforms, find threads/posts/videos that match the keyword profile,
emit candidate rows for the Content agent to draft. **Never posts. Never drafts.**
The whole point of the recon split is that this phase has zero posting risk and
can run on cron without the kill-switch gate.

Today this implements Reddit only (`scout_reddit`). YouTube / HN / GitHub
sources land in later slices — see plans/2026-05-09-outreach-pipeline-multi-agent.md.

Reuses the existing reddit scout helpers in reddit_outreach (fetch_subreddit_rules,
fetch_hot_threads, fetch_thread_comments, thread_is_relevant) — those functions
were always platform-read-only with no servo coupling, so wrapping them is safe.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.services.social_outreach import audit, kill_switch
from backend.services.social_outreach.reddit_outreach import (
    REDDIT_BASE,
    fetch_hot_threads,
    fetch_subreddit_rules,
    fetch_thread_comments,
    is_self_promo_banned,
    thread_is_relevant,
)

logger = logging.getLogger(__name__)


CANDIDATE_DEDUPE_STATUSES = ["candidate", "drafted", "approved", "posted"]
"""When deduping recon output, treat any of these as "already touched" so we
don't re-scout the same thread. "rejected" and "aborted" are excluded — those
are dead-ends and we want them re-scouted in case the rejection was transient."""

DEFAULT_CANDIDATES_PER_PASS = 3
"""How many candidates a single pass will emit at most. Recon is cheap so we
can be greedy, but keep it small enough that human review of the queue stays
tractable."""


class RecondAgent:
    """One pass = scout one platform/sub, emit up to N candidate rows.

    Stateless — safe to call from a celery task. All state lives in the audit
    rows the pass writes.
    """

    def scout_reddit(
        self,
        subreddit: str,
        max_candidates: int = DEFAULT_CANDIDATES_PER_PASS,
    ) -> dict:
        """Scout one subreddit's hot list, emit candidate rows for relevant threads.

        Returns a report dict suitable for celery to log:
            {
                "platform": "reddit",
                "subreddit": str,
                "candidates": int,        # rows emitted
                "skipped_dedupe": int,    # already-touched threads
                "skipped_irrelevant": int,
                "reason": Optional[str],  # set when the whole pass is no-op
            }
        """
        report = {
            "platform": "reddit",
            "subreddit": subreddit,
            "candidates": 0,
            "skipped_dedupe": 0,
            "skipped_irrelevant": 0,
            "reason": None,
        }

        # The kill-switch gates posting; recon is read-only. We still respect
        # it as a global "outreach paused" signal so a single env flip stops
        # all phases consistently. Easy to split later if we want recon to
        # keep running while posting is paused.
        if not kill_switch.is_enabled():
            report["reason"] = "kill_switch_off"
            return report

        rules_text = "\n".join(fetch_subreddit_rules(subreddit))
        ban_match = is_self_promo_banned(rules_text)
        if ban_match:
            # We skip even at recon time — no point queueing candidates we'd
            # have to reject in Content for promo-policy reasons.
            report["reason"] = f"sub_bans_self_promo:{ban_match}"
            return report

        threads = fetch_hot_threads(subreddit)
        if not threads:
            report["reason"] = "no_hot_threads"
            return report

        already_touched = audit.recent_thread_ids(
            "reddit", statuses=CANDIDATE_DEDUPE_STATUSES,
        )

        for thread in threads:
            if report["candidates"] >= max_candidates:
                break
            if thread.id in already_touched:
                report["skipped_dedupe"] += 1
                continue

            comments = fetch_thread_comments(thread.permalink)
            feature_hint = thread_is_relevant(thread, comments)
            if feature_hint is None:
                report["skipped_irrelevant"] += 1
                continue

            # Recon-stage payload — Content agent will replace this JSON with
            # the actual draft text. Keeps everything in existing columns,
            # avoids a schema migration.
            extras = {
                "title": thread.title,
                "score": thread.score,
                "num_comments": thread.num_comments,
                "selftext_preview": thread.selftext[:400] if thread.selftext else "",
            }
            audit_id = audit.log_candidate(
                platform="reddit",
                action="comment",
                target_url=thread.permalink,
                target_thread_id=thread.id,
                feature_hint=feature_hint,
                # Recon "score" — for now the simple heuristic of upvote count
                # normalized to the 1k mark. Content agent's grade_score will
                # overwrite this. Better recon scoring can come later.
                score=min(1.0, thread.score / 1000.0),
                extras=extras,
            )
            if audit_id is not None:
                report["candidates"] += 1
                logger.info(
                    "recon: queued candidate r/%s thread=%s feature=%s score=%d",
                    subreddit, thread.id, feature_hint, thread.score,
                )

        return report
