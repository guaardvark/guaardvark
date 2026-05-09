"""
Recon agent — Phase 1 of the multi-agent outreach pipeline.

Job: scan platforms, find threads/posts/videos that match the keyword profile,
emit candidate rows for the Content agent to draft. **Never posts. Never drafts.**
The whole point of the recon split is that this phase has zero posting risk and
can run on cron without the kill-switch gate.

Today this implements Reddit (`scout_reddit`) and YouTube (`scout_youtube`).
HN / GitHub sources land in later slices —
see plans/2026-05-09-outreach-pipeline-multi-agent.md.

Reuses the existing reddit scout helpers in reddit_outreach (fetch_subreddit_rules,
fetch_hot_threads, fetch_thread_comments, thread_is_relevant) — those functions
were always platform-read-only with no servo coupling, so wrapping them is safe.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from backend.services.social_outreach import audit, external_grader, kill_switch, persona
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

MIN_RELEVANCE_GRADE = 0.5
"""Threshold for the LLM relevance judge. The keyword filter is regex-only and
can't tell "I love local AI" from "I hate local AI" — the LLM sees context
and rules out hostile/off-topic threads. Below this we skip without queuing.
Same skipped-as-pass behavior as the Content grader: if the relevance model
is unavailable we fall through to keyword-only behavior."""


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
            "skipped_by_llm": 0,
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

            # LLM relevance judge — the keyword match got us here, but it
            # can't tell "I love qwen" from "qwen is broken trash" or
            # "OP already solved it, no comment needed". A short LLM call
            # spends a second to filter out the false positives that would
            # otherwise burn drafting tokens later. Skipped-as-pass behavior
            # if the model isn't loaded.
            relevance = external_grader.score_thread_relevance(
                title=thread.title,
                selftext=thread.selftext,
                top_comments=comments,
                feature_hint=feature_hint,
                subreddit=subreddit,
            )
            if not relevance.get("skipped") and relevance.get("grade", 0.0) < MIN_RELEVANCE_GRADE:
                report["skipped_by_llm"] += 1
                logger.info(
                    "recon: r/%s thread=%s skipped by LLM (grade=%.2f, reason=%s)",
                    subreddit, thread.id, relevance.get("grade", 0.0),
                    relevance.get("reason", "")[:80],
                )
                continue

            # Recon-stage payload — Content agent reads this and drafts
            # without a live Reddit API call. Stores enough context that
            # each phase is self-contained. Caps comment length to keep
            # rows reasonable; the LLM doesn't need huge comments to draft.
            extras = {
                "title": thread.title,
                "score": thread.score,
                "num_comments": thread.num_comments,
                "selftext_preview": thread.selftext[:400] if thread.selftext else "",
                "top_comments": [c[:600] for c in comments[:5]],
                # Content reads these to adjust tone (per-sub voice) and
                # timeliness framing (a 30-min-old thread is hot, a 12-hour
                # old one isn't). created_utc is unix seconds; Content
                # converts to a human-readable "age" string.
                "subreddit": subreddit,
                "created_utc": thread.created_utc,
                # Preserve the LLM's relevance verdict so Content has the
                # original "why we picked this thread" rationale available
                # in the prompt context if it wants it.
                "relevance_grade": relevance.get("grade"),
                "relevance_reason": relevance.get("reason", "")[:200],
                "relevance_skipped": relevance.get("skipped", False),
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

    def scout_youtube(
        self,
        keyword_profile: str,
        max_candidates: int = DEFAULT_CANDIDATES_PER_PASS,
    ) -> dict:
        """Scout YouTube via web_search for videos matching a keyword profile.

        Builds `site:youtube.com {keyword_profile}` query, runs it through
        the same enhanced_web_search path the chat tools use, filters down
        to actual /watch?v= video URLs, and emits candidate rows.

        Returns a report dict mirroring scout_reddit:
            {
                "platform": "youtube",
                "keyword_profile": str,
                "candidates": int,
                "skipped_dedupe": int,
                "skipped_irrelevant": int,
                "skipped_by_llm": int,
                "skipped_non_video": int,    # DDG sometimes returns channels / search pages
                "reason": Optional[str],
            }

        No servo, no posting. Safe to run on cron — same kill-switch gate
        as scout_reddit so a single env flip pauses all phases.
        """
        report = {
            "platform": "youtube",
            "keyword_profile": keyword_profile,
            "candidates": 0,
            "skipped_dedupe": 0,
            "skipped_irrelevant": 0,
            "skipped_by_llm": 0,
            "skipped_non_video": 0,
            "reason": None,
        }

        if not kill_switch.is_enabled():
            report["reason"] = "kill_switch_off"
            return report

        # Lazy import — web_search lives in the API layer and pulling it at
        # module import time would couple recon to the Flask blueprint stack.
        # Keeping it inside the function lets the test suite mock the symbol
        # at the recon module path without dragging the whole web layer in.
        from backend.api.web_search_api import enhanced_web_search

        query = f"site:youtube.com {keyword_profile}"
        try:
            search = enhanced_web_search(query)
        except Exception as e:
            # Same skipped-as-pass-through pattern as the LLM grader: an infra
            # blip on a single tick shouldn't tear down the whole recon pass.
            logger.warning("recon: youtube web_search failed for %r: %s", query, e)
            report["reason"] = f"web_search_failed: {e}"
            return report

        # Defensive: enhanced_web_search returns a dict in the happy path,
        # but a future refactor or a backend-layer error could surface a
        # bare string or list. .get on a non-dict would crash the celery
        # task; isinstance check turns it into a clean skip-with-reason.
        if not isinstance(search, dict) or not search.get("success"):
            report["reason"] = "web_search_no_results"
            return report

        results = (search.get("data") or {}).get("results") or []
        if not isinstance(results, list) or not results:
            report["reason"] = "web_search_empty_results"
            return report

        already_touched = audit.recent_thread_ids(
            "youtube", statuses=CANDIDATE_DEDUPE_STATUSES,
        )
        # Track ids emitted in THIS pass too — DDG can return the same
        # video under two URL shapes (e.g. youtu.be/X and youtube.com/watch?v=X)
        # which both map to the same video_id but bypass the persistent
        # dedupe set since neither is in audit yet. (Caught in review.)
        already_touched = set(already_touched)

        for idx, result in enumerate(results):
            if report["candidates"] >= max_candidates:
                break
            if not isinstance(result, dict):
                report["skipped_non_video"] += 1
                continue
            raw_url = (result.get("url") or "").strip()
            video_id = _extract_youtube_video_id(raw_url)
            if not video_id:
                # DDG occasionally surfaces channel pages, playlists, or the
                # YouTube search-results page itself. None of those are
                # commentable videos, so skip cleanly.
                report["skipped_non_video"] += 1
                continue
            if video_id in already_touched:
                report["skipped_dedupe"] += 1
                continue

            # Canonicalize the URL we store. Trusting DDG's raw URL string
            # would let a malicious/compromised search response inject XSS
            # (javascript:..., data:...) into target_url, which the UI
            # later renders as <a href>. Reconstructing from the regex-
            # validated 11-char id eliminates that surface entirely.
            target_url = f"https://www.youtube.com/watch?v={video_id}"

            title = (result.get("title") or "").strip()
            snippet = (result.get("snippet") or "").strip()
            haystack = f"{title}\n{snippet}"
            feature_hint = persona.find_relevant_feature(haystack)
            if feature_hint is None:
                # site: filter trusts DDG to keep us on YouTube but the
                # keyword filter still has to confirm the video is about
                # something we can credibly comment on. False positives
                # ("LocalLLaMA" matching a non-AI gaming clip titled
                # "local llama farm") get caught here.
                report["skipped_irrelevant"] += 1
                continue

            # Relevance grader is reddit-shaped in its system prompt but
            # the judgment ("would commenting here be a good fit?")
            # generalizes. Pass the snippet as the body and an empty
            # comments list since DDG doesn't expose comments.
            relevance = external_grader.score_thread_relevance(
                title=title,
                selftext=snippet,
                top_comments=[],
                feature_hint=feature_hint,
                subreddit="",
            )
            if not relevance.get("skipped") and relevance.get("grade", 0.0) < MIN_RELEVANCE_GRADE:
                report["skipped_by_llm"] += 1
                logger.info(
                    "recon: youtube vid=%s skipped by LLM (grade=%.2f, reason=%s)",
                    video_id, relevance.get("grade", 0.0),
                    relevance.get("reason", "")[:80],
                )
                continue

            # Rank-decay scoring — DDG's first result is the strongest
            # signal we have at recon time (no view counts from the
            # search API). Content agent's grade overwrites this.
            rank_score = max(0.1, 1.0 - (idx / max(1, len(results))))

            extras = {
                "title": title,
                "snippet": snippet[:600],
                # Phase 2's _build_thread_context reads `selftext_preview`
                # to fill the "OP BODY" slot of the LLM draft prompt. The
                # DDG snippet is the closest YouTube equivalent (the visible
                # video description preview), so alias it here. Without
                # this, YouTube candidates would draft from title alone and
                # the prompt's "OP BODY" line would default to "(link-only
                # post)" — gutted context. (Caught in slice-6 review.)
                "selftext_preview": snippet[:600],
                "search_query": query,
                "rank": idx,
                "video_id": video_id,
                "relevance_grade": relevance.get("grade"),
                "relevance_reason": relevance.get("reason", "")[:200],
                "relevance_skipped": relevance.get("skipped", False),
            }
            audit_id = audit.log_candidate(
                platform="youtube",
                action="comment",
                target_url=target_url,
                target_thread_id=video_id,
                feature_hint=feature_hint,
                score=rank_score,
                extras=extras,
            )
            if audit_id is not None:
                report["candidates"] += 1
                # In-pass dedupe: a future result in the same DDG response
                # might be a different URL shape for the same video — adding
                # the id here keeps the next iteration from emitting a
                # duplicate row.
                already_touched.add(video_id)
                logger.info(
                    "recon: queued youtube candidate vid=%s feature=%s rank=%d",
                    video_id, feature_hint, idx,
                )

        return report


_YOUTUBE_VIDEO_ID_RE = re.compile(
    # All five shapes below are commentable single-video URLs. Shorts and
    # /live/ pages were missed in the first cut — Shorts surface for short-
    # tail queries and Live archives are full videos with comment threads,
    # so dropping them costs real candidates. (Caught in slice-6 review.)
    r"(?:youtube\.com/watch\?(?:[^ ]*&)?v="
    r"|youtu\.be/"
    r"|youtube\.com/embed/"
    r"|youtube\.com/v/"
    r"|youtube\.com/shorts/"
    r"|youtube\.com/live/)([A-Za-z0-9_-]{11})"
)


def _extract_youtube_video_id(url: str) -> Optional[str]:
    """Pull the 11-char video id out of a YouTube URL, or None if the URL
    isn't a video page. Filters out channel/playlist/search URLs so recon
    doesn't try to comment on a homepage. Matches /watch?v=, /shorts/,
    /live/, youtu.be/, /embed/, /v/.
    """
    if not url:
        return None
    m = _YOUTUBE_VIDEO_ID_RE.search(url)
    return m.group(1) if m else None
