"""
Recon agent — Phase 1 of the multi-agent outreach pipeline.

Job: scan platforms, find threads/posts/videos that match the keyword profile,
emit candidate rows for the Content agent to draft. **Never posts. Never drafts.**
The whole point of the recon split is that this phase has zero posting risk and
can run on cron without the kill-switch gate.

Today this implements Reddit (`scout_reddit`), YouTube (`scout_youtube`) and
Facebook groups (`scout_facebook_groups`).
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

from backend.services.social_outreach import audit, external_grader, kill_switch, persona, profile
from backend.services.social_outreach.reddit_outreach import (
    REDDIT_BASE,
    fetch_hot_threads,
    fetch_subreddit_rules,
    fetch_thread,
    fetch_thread_comments,
    is_self_promo_banned,
    reading_problem,
    reddit_reading,
    search_thread_refs,
    thread_is_relevant,
)
from backend.utils.settings_utils import web_access_block_reason

logger = logging.getLogger(__name__)


CANDIDATE_DEDUPE_STATUSES = ["candidate", "drafted", "approved", "posted"]
"""When deduping recon output, treat any of these as "already touched" so we
don't re-scout the same thread. "rejected" and "aborted" are excluded — those
are dead-ends and we want them re-scouted in case the rejection was transient."""

DEFAULT_CANDIDATES_PER_PASS = 3
"""How many candidates a single pass will emit at most. Recon is cheap so we
can be greedy, but keep it small enough that human review of the queue stays
tractable."""

NO_FEATURES_REASON = "no_features_configured"
"""Report reason when the outreach profile lists no features: the keyword
filter has nothing to match, so a pass would skip every thread as irrelevant.
Add features with keywords under "What you promote" on the Outreach page."""

MIN_RELEVANCE_GRADE = 0.5
"""Threshold for the LLM relevance judge. The keyword filter is regex-only and
can't tell "I love local AI" from "I hate local AI" — the LLM sees context
and rules out hostile/off-topic threads. Below this, or on a "skip" verdict
at any grade, we skip without queuing (judged_unfit). The scheduled Reddit loop (reddit_outreach.RedditOutreachLoop) skips on the
same bar. When the relevance model is unavailable, recon still queues the
candidate on its keyword match, marked relevance_skipped, and the loop still
drafts, marked relevance unchecked; either way the draft waits for a
person's approval, like every draft."""


def judged_unfit(relevance: dict) -> bool:
    """True when the thread-fit judge ran and said no: a grade below
    MIN_RELEVANCE_GRADE, or a "skip" verdict whatever grade came with it.
    A skipped judge is no verdict either way."""
    if relevance.get("skipped"):
        return False
    verdict = str(relevance.get("verdict") or "").strip().lower()
    return verdict == "skip" or relevance.get("grade", 0.0) < MIN_RELEVANCE_GRADE


def _whole_word_in(term: str, blob: str) -> bool:
    """True when ``term`` occurs in ``blob`` as a whole word or phrase: not
    inside a longer word ("art" is not in "start"), a plural "s" allowed."""
    words = [re.escape(w) for w in term.split()]
    if not words:
        return False
    pattern = r"(?<![a-z0-9])" + r"\s+".join(words) + r"s?(?![a-z0-9])"
    return re.search(pattern, blob) is not None


def topic_matches_text(text: str, topic_filters: Optional[list[str]]) -> bool:
    """True when no filters are set, or any topic, or any 3+ letter token of
    one, appears in ``text`` as a whole word.

    Used so NL phrases like "market on reddit about ComfyUI" actually constrain
    which hot threads become candidates (after the profile keyword
    match). A cheap pre-filter; the thread-fit judge decides. Empty/None
    filters → pass-through (legacy behavior).
    """
    if not topic_filters:
        return True
    blob = (text or "").lower()
    if not blob:
        return False
    for topic in topic_filters:
        t = (topic or "").strip().lower()
        if not t:
            continue
        if _whole_word_in(t, blob):
            return True
        for tok in re.split(r"[\s\-/_,]+", t):
            if len(tok) >= 3 and _whole_word_in(tok, blob):
                return True
    return False


def prefer_subreddit_for_topics(
    subs: list[str],
    topics: Optional[list[str]],
) -> Optional[str]:
    """Pick the first configured sub whose name overlaps an NL topic.

    Falls back to None so callers can use round-robin ``_next_target``.
    """
    if not topics or not subs:
        return None
    for sub in subs:
        sl = (sub or "").strip().lower()
        if not sl:
            continue
        for topic in topics:
            t = (topic or "").strip().lower()
            if not t:
                continue
            if t in sl or sl in t:
                return sub
            for tok in re.split(r"[\s\-/_,]+", t):
                if len(tok) >= 3 and tok in sl:
                    return sub
    return None


class RecondAgent:
    """One pass = scout one platform/sub, emit up to N candidate rows.

    Stateless — safe to call from a celery task. All state lives in the audit
    rows the pass writes.
    """

    def scout_reddit(
        self,
        subreddit: str,
        max_candidates: int = DEFAULT_CANDIDATES_PER_PASS,
        topic_filters: Optional[list[str]] = None,
    ) -> dict:
        """Scout one subreddit's hot list, emit candidate rows for relevant threads.

        Returns a report dict suitable for celery to log:
            {
                "platform": "reddit",
                "subreddit": str,
                "candidates": int,        # rows emitted
                "skipped_dedupe": int,    # already-touched threads
                "skipped_irrelevant": int,
                "skipped_topic": int,     # failed NL topic_filters overlap
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
            "skipped_topic": 0,
            "reason": None,
        }

        # The kill-switch gates posting; recon is read-only. We still respect
        # it as a global "outreach paused" signal so a single env flip stops
        # all phases consistently. Easy to split later if we want recon to
        # keep running while posting is paused.
        if not kill_switch.is_enabled():
            report["reason"] = "kill_switch_off"
            return report
        # Keywords alone are enough to match a thread; a blurb is optional.
        if not profile.load().features:
            report["reason"] = NO_FEATURES_REASON
            return report
        # Reading Reddit is an outside request: the same gate as the YouTube
        # scout and posting (Settings, web access; off by default).
        blocked = web_access_block_reason("scout Reddit for outreach")
        if blocked:
            logger.info("recon: reddit pass skipped: %s", blocked)
            report["reason"] = "web_access_off"
            return report

        with reddit_reading():
            return self._scout_reddit_threads(subreddit, max_candidates, topic_filters, report)

    def _scout_reddit_threads(
        self,
        subreddit: str,
        max_candidates: int,
        topic_filters: Optional[list[str]],
        report: dict,
    ) -> dict:
        closed = self._subreddit_closed(subreddit)
        if closed:
            report["reason"] = closed
            if closed == "rules_unreadable":
                report["detail"] = reading_problem()
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
            self._consider_thread(thread, subreddit, topic_filters, report)
        return report

    @staticmethod
    def _subreddit_closed(subreddit: str) -> Optional[str]:
        """Why outreach leaves ``subreddit`` alone, or None when it may comment
        there: its rules could not be read, or they ban self-promotion."""
        rules = fetch_subreddit_rules(subreddit)
        if rules is None:
            # Unread rules are not "no rules": skip the community this pass.
            return "rules_unreadable"
        ban_match = is_self_promo_banned("\n".join(rules))
        if ban_match:
            # No point queueing candidates Content would have to reject for
            # promo-policy reasons.
            return f"sub_bans_self_promo:{ban_match}"
        return None

    def scout_reddit_search(
        self,
        queries: list[str],
        subreddit: Optional[str] = None,
        max_candidates: int = DEFAULT_CANDIDATES_PER_PASS,
    ) -> dict:
        """Scout Reddit's search for ``queries`` (within ``subreddit`` when one
        is given) and emit candidate rows for the threads that fit.

        Each thread goes through what the hot-list scout applies: its
        community's rules must be readable and allow promotion, the thread must
        match the profile and pass the thread-fit judge. Needs the agent
        browser, the only reader Reddit serves search to.
        """
        report = {
            "platform": "reddit",
            "search": list(queries),
            "subreddit": subreddit,
            "threads_found": 0,
            "candidates": 0,
            "skipped_dedupe": 0,
            "skipped_irrelevant": 0,
            "skipped_by_llm": 0,
            "skipped_topic": 0,
            "skipped_rules": 0,
            "reason": None,
        }
        if not kill_switch.is_enabled():
            report["reason"] = "kill_switch_off"
            return report
        if not profile.load().features:
            report["reason"] = NO_FEATURES_REASON
            return report
        blocked = web_access_block_reason("scout Reddit for outreach")
        if blocked:
            logger.info("recon: reddit search skipped: %s", blocked)
            report["reason"] = "web_access_off"
            return report

        with reddit_reading():
            already_touched = set(audit.recent_thread_ids(
                "reddit", statuses=CANDIDATE_DEDUPE_STATUSES,
            ))
            closed: dict[str, Optional[str]] = {}
            for query in queries:
                if report["candidates"] >= max_candidates:
                    break
                for sub, thread_id, permalink in search_thread_refs(query, subreddit):
                    if report["candidates"] >= max_candidates:
                        break
                    report["threads_found"] += 1
                    if thread_id in already_touched:
                        report["skipped_dedupe"] += 1
                        continue
                    already_touched.add(thread_id)
                    if sub not in closed:
                        closed[sub] = self._subreddit_closed(sub)
                    if closed[sub]:
                        report["skipped_rules"] += 1
                        continue
                    thread = fetch_thread(permalink)
                    if thread is None:
                        report["skipped_irrelevant"] += 1
                        continue
                    self._consider_thread(thread, thread.subreddit or sub, None, report)
            if not report["threads_found"]:
                report["reason"] = "no_search_results"
                report["detail"] = reading_problem()
        return report

    def _consider_thread(
        self,
        thread,
        subreddit: str,
        topic_filters: Optional[list[str]],
        report: dict,
    ) -> None:
        """Emit a candidate row for ``thread`` if it fits; count why not in
        ``report`` otherwise."""
        comments = fetch_thread_comments(thread.permalink)
        feature_hint = thread_is_relevant(thread, comments)
        if feature_hint is None:
            report["skipped_irrelevant"] += 1
            return

        # NL topic filter (optional) — e.g. "ComfyUI" from chat intent.
        # The profile keyword match already passed; this narrows further.
        blob = f"{thread.title} {thread.selftext or ''}"
        if not topic_matches_text(blob, topic_filters):
            report["skipped_topic"] += 1
            return

        # LLM relevance judge — the keyword match got us here, but it
        # can't tell "I love ollama" from "ollama is broken trash" or
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
        if judged_unfit(relevance):
            report["skipped_by_llm"] += 1
            logger.info(
                "recon: r/%s thread=%s skipped by LLM (grade=%.2f, reason=%s)",
                subreddit, thread.id, relevance.get("grade", 0.0),
                relevance.get("reason", "")[:80],
            )
            return

        # Recon-stage payload — Content agent reads this and drafts
        # without a live Reddit API call. Stores enough context that
        # each phase is self-contained. Caps comment length to keep
        # rows reasonable; the LLM doesn't need huge comments to draft.
        extras = {
            "title": thread.title,
            "score": thread.score,
            "num_comments": thread.num_comments,
            "selftext_preview": thread.selftext[:400] if thread.selftext else "",
            # The post as its approver reads it; the drafter works from the preview.
            "source_text": (thread.selftext or "")[:audit.SOURCE_TEXT_MAX],
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


    def scout_facebook_groups(
        self,
        groups: Optional[list[str]] = None,
        max_candidates: int = DEFAULT_CANDIDATES_PER_PASS,
        topic_filters: Optional[list[str]] = None,
    ) -> dict:
        """Scout the newest posts of Facebook groups the account is in and
        emit a candidate row for each post that fits.

        ``groups`` are group ids or names-in-address; without any, the groups
        the account is in whose name matches a profile keyword are read. A
        post goes through what a Reddit thread does: the group's rules must
        be readable and allow promotion, the post must match the profile and
        pass the fit judge. Reads through the agent browser, signed in to
        Facebook, and is skipped while the agent is using it.
        """
        from backend.services.social_outreach import facebook_outreach as fb
        from backend.services.social_outreach.reddit_outreach import bidi_reachable, is_self_promo_banned
        from backend.utils.agent_browser_control import agent_browser_control

        report = {
            "platform": "facebook",
            "groups": [],
            "posts_seen": 0,
            "candidates": 0,
            "skipped_dedupe": 0,
            "skipped_irrelevant": 0,
            "skipped_by_llm": 0,
            "skipped_topic": 0,
            "skipped_rules": 0,
            "skipped_answered": 0,
            "reason": None,
        }
        if not kill_switch.is_enabled():
            report["reason"] = "kill_switch_off"
            return report
        if not profile.load().features:
            report["reason"] = NO_FEATURES_REASON
            return report
        blocked = web_access_block_reason("scout Facebook groups for outreach")
        if blocked:
            logger.info("recon: facebook pass skipped: %s", blocked)
            report["reason"] = "web_access_off"
            return report

        with agent_browser_control() as refusal:
            if refusal:
                report["reason"] = "browser_in_use"
                report["detail"] = str(refusal)
                return report
            ok, why = bidi_reachable()
            if not ok:
                report["reason"] = "browser_unreadable"
                report["detail"] = why
                return report

            wanted_groups = [g for g in (fb.group_ref(g) for g in groups or []) if g]
            if not wanted_groups:
                wanted_groups = [
                    group for group, name in fb.joined_groups()
                    if persona.find_relevant_feature(name) is not None
                ]
            if not wanted_groups:
                report["reason"] = "no_groups"
                return report

            already_touched = set(audit.recent_thread_ids("facebook", statuses=CANDIDATE_DEDUPE_STATUSES))
            for group in wanted_groups:
                if report["candidates"] >= max_candidates:
                    break
                entry = {"group": group, "name": "", "posts_seen": 0, "candidates": 0, "reason": None}
                report["groups"].append(entry)
                rules = fb.read_group_rules(group)
                if rules is None:
                    entry["reason"] = "rules_unreadable"
                    report["skipped_rules"] += 1
                    continue
                ban_match = is_self_promo_banned(rules)
                if ban_match:
                    entry["reason"] = f"group_bans_self_promo:{ban_match}"
                    report["skipped_rules"] += 1
                    continue

                def fits(text: str) -> bool:
                    return (
                        persona.find_relevant_feature(text) is not None
                        and topic_matches_text(text, topic_filters)
                    )

                name, posts, counts = fb.read_group_feed(
                    group, fits, max_candidates - report["candidates"], skip_post_ids=already_touched,
                )
                entry.update(name=name, posts_seen=counts["seen"], reason=counts.get("problem"))
                report["posts_seen"] += counts["seen"]
                report["skipped_irrelevant"] += counts["unwanted"] + counts["no_address"]
                report["skipped_dedupe"] += counts["known"]
                for post in posts:
                    already_touched.add(post.post_id)
                    if self._consider_group_post(post, name, report):
                        entry["candidates"] += 1
        return report

    def scout_facebook_post(self, url: str) -> dict:
        """Emit a candidate row for the one group post at ``url``, which a
        person named: it is read and queued without the keyword match or the
        fit judge."""
        from backend.services.social_outreach import facebook_outreach as fb
        from backend.services.social_outreach.reddit_outreach import bidi_reachable
        from backend.utils.agent_browser_control import agent_browser_control

        report = {"platform": "facebook", "post": url, "candidates": 0, "reason": None}
        if not kill_switch.is_enabled():
            report["reason"] = "kill_switch_off"
            return report
        blocked = web_access_block_reason("read the Facebook post at that link")
        if blocked:
            report["reason"] = "web_access_off"
            return report
        with agent_browser_control() as refusal:
            if refusal:
                report.update(reason="browser_in_use", detail=str(refusal))
                return report
            ok, why = bidi_reachable()
            if not ok:
                report.update(reason="browser_unreadable", detail=why)
                return report
            post, why = fb.read_group_post(url)
        if post is None:
            report.update(reason="post_unreadable", detail=why)
            return report
        if not post.actor:
            report["reason"] = "post_takes_no_comment"
            return report
        audit_id = audit.log_candidate(
            platform="facebook",
            action="comment",
            target_url=post.url,
            target_thread_id=post.post_id,
            feature_hint=persona.find_relevant_feature(post.text) or "",
            extras={
                "title": "",
                "selftext_preview": post.text[:1200],
                "source_text": post.text[:audit.SOURCE_TEXT_MAX],
                "top_comments": post.comments[:5],
                "author": post.author,
                "community": post.group_name,
                "group": post.group,
                "posts_as": post.actor,
                "relevance_reason": "You gave this post's link.",
                "relevance_skipped": True,
            },
        )
        if audit_id is not None:
            report["candidates"] = 1
        return report

    def _consider_group_post(self, preview, group_name: str, report: dict) -> bool:
        """Read the whole post and emit a candidate row if it fits; count why
        not in ``report`` otherwise."""
        from backend.services.social_outreach import facebook_outreach as fb

        post, why = fb.read_group_post(preview.url)
        if post is None:
            logger.info("recon: facebook post %s not read: %s", preview.url, why)
            report["skipped_irrelevant"] += 1
            return False
        if not post.actor or post.answered:
            # No comment box for this account, or it has commented already.
            report["skipped_answered"] += 1
            return False
        feature_hint = persona.find_relevant_feature("\n".join([post.text, *post.comments[:5]]))
        if feature_hint is None:
            report["skipped_irrelevant"] += 1
            return False

        group_name = post.group_name or group_name
        title = post.text.split("\n", 1)[0][:200]
        relevance = external_grader.score_thread_relevance(
            title=title,
            selftext=post.text,
            top_comments=post.comments,
            feature_hint=feature_hint,
            community=f"Facebook group: {group_name}" if group_name else "Facebook group",
        )
        if judged_unfit(relevance):
            report["skipped_by_llm"] += 1
            logger.info(
                "recon: facebook post %s skipped by LLM (grade=%.2f, reason=%s)",
                post.post_id, relevance.get("grade", 0.0), relevance.get("reason", "")[:80],
            )
            return False

        extras = {
            # A group post has no title; the drafter and the approver read the text.
            "title": "",
            "selftext_preview": post.text[:1200],
            "source_text": post.text[:audit.SOURCE_TEXT_MAX],
            "top_comments": post.comments[:5],
            "author": post.author,
            "community": group_name,
            "group": post.group,
            "posts_as": post.actor,
            "relevance_grade": relevance.get("grade"),
            "relevance_reason": relevance.get("reason", "")[:200],
            "relevance_skipped": relevance.get("skipped", False),
        }
        audit_id = audit.log_candidate(
            platform="facebook",
            action="comment",
            target_url=post.url,
            target_thread_id=post.post_id,
            feature_hint=feature_hint,
            score=relevance.get("grade"),
            extras=extras,
        )
        if audit_id is None:
            return False
        report["candidates"] += 1
        logger.info("recon: queued facebook candidate group=%s post=%s feature=%s", post.group, post.post_id, feature_hint)
        return True

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
                "skipped_non_video": int,    # the search engine sometimes returns channels / search pages
                "reason": Optional[str],
            }

        No servo, no posting. Safe to run on cron — same kill-switch gate
        as scout_reddit so a single env flip pauses all phases. The query goes
        to the web search engine, so the pass also needs web access on in
        Settings (off by default), as the web_search tool does; with it off
        the report's reason is "web_access_off" and nothing is sent.
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
        # Keywords alone are enough to match a thread; a blurb is optional.
        if not profile.load().features:
            report["reason"] = NO_FEATURES_REASON
            return report

        blocked = web_access_block_reason("scout YouTube for outreach")
        if blocked:
            logger.info("recon: youtube pass skipped: %s", blocked)
            report["reason"] = "web_access_off"
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
        # Track ids emitted in THIS pass too — the search engine can return the same
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
                # The search engine occasionally surfaces channel pages, playlists, or the
                # YouTube search-results page itself. None of those are
                # commentable videos, so skip cleanly.
                report["skipped_non_video"] += 1
                continue
            if video_id in already_touched:
                report["skipped_dedupe"] += 1
                continue

            # Canonicalize the URL we store. Trusting the search engine's raw URL string
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
                # site: filter trusts the search engine to keep us on YouTube but the
                # keyword filter still has to confirm the video is about
                # something we can credibly comment on. False positives
                # ("LocalLLaMA" matching a non-AI gaming clip titled
                # "local llama farm") get caught here.
                report["skipped_irrelevant"] += 1
                continue

            # Relevance grader is reddit-shaped in its system prompt but
            # the judgment ("would commenting here be a good fit?")
            # generalizes. Pass the snippet as the body and an empty
            # comments list since a search result carries no comments.
            relevance = external_grader.score_thread_relevance(
                title=title,
                selftext=snippet,
                top_comments=[],
                feature_hint=feature_hint,
                subreddit="",
            )
            if judged_unfit(relevance):
                report["skipped_by_llm"] += 1
                logger.info(
                    "recon: youtube vid=%s skipped by LLM (grade=%.2f, reason=%s)",
                    video_id, relevance.get("grade", 0.0),
                    relevance.get("reason", "")[:80],
                )
                continue

            # Rank-decay scoring — the search engine's first result is the strongest
            # signal we have at recon time (no view counts from the
            # search API). Content agent's grade overwrites this.
            rank_score = max(0.1, 1.0 - (idx / max(1, len(results))))

            extras = {
                "title": title,
                "snippet": snippet[:600],
                # Phase 2's _build_thread_context reads `selftext_preview`
                # to fill the "OP BODY" slot of the LLM draft prompt. The
                # search snippet is the closest YouTube equivalent (the visible
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
                # In-pass dedupe: a future result in the same search response
                # might be a different URL shape for the same video — adding
                # the id here keeps the next iteration from emitting a
                # duplicate row.
                already_touched.add(video_id)
                logger.info(
                    "recon: queued youtube candidate vid=%s feature=%s rank=%d",
                    video_id, feature_hint, idx,
                )

        return report


    def scout_youtube_my_video_replies(
        self,
        monitored_videos: Optional[list[str]] = None,
        max_candidates: int = DEFAULT_CANDIDATES_PER_PASS,
    ) -> dict:
        """Scout the agent's own YouTube videos for replies left under the
        account's own comments. Emits candidate rows with action="reply" so the Content agent
        can draft a response and the dispatcher can route it through
        post_youtube_reply_via_servo.

        `monitored_videos` is a list of canonical youtube.com/watch URLs to
        scan. Defaults to `data/agent/social_outreach_targets.json::youtube.monitored_videos`
        if not supplied. Each URL goes through the same dedupe/grading
        machinery as scout_youtube; "thread id" for a reply candidate is
        the 11-char video id concatenated with a short hash of the parent
        comment so multiple parents on the same video don't collide.

        Returns report dict mirroring scout_youtube:
            {
                "platform": "youtube",
                "action": "reply",
                "videos_scanned": int,
                "candidates": int,
                "skipped_dedupe": int,
                "skipped_no_replies": int,
                "skipped_irrelevant": int,
                "reason": Optional[str],
            }

        Implementation note (2026-05-13 marble-block pass):
          The `_fetch_recent_replies_to_own_comments(video_url, handle)` helper is
          stubbed — it returns [] until the servo-driven scrape is wired
          in the next chip-the-rough-edges pass. The scout still runs
          safely (it just emits 0 candidates on every tick) and the
          downstream pipeline (Content/grader/dispatcher) is fully
          unblocked because `enqueue_youtube_reply_candidate()` lets you
          hand-seed a reply candidate row at any time.
        """
        report = {
            "platform": "youtube",
            "action": "reply",
            "videos_scanned": 0,
            "candidates": 0,
            "skipped_dedupe": 0,
            "skipped_no_replies": 0,
            "skipped_irrelevant": 0,
            "reason": None,
        }

        if not kill_switch.is_enabled():
            report["reason"] = "kill_switch_off"
            return report

        handle = profile.load().account_handle
        if not handle:
            report["reason"] = "no_account_handle"
            return report

        if monitored_videos is None:
            monitored_videos = _load_monitored_videos()
        if not monitored_videos:
            report["reason"] = "no_monitored_videos_configured"
            return report

        already_touched = set(audit.recent_thread_ids(
            "youtube", statuses=CANDIDATE_DEDUPE_STATUSES,
        ))

        for video_url in monitored_videos:
            if report["candidates"] >= max_candidates:
                break
            video_id = _extract_youtube_video_id(video_url)
            if not video_id:
                continue
            report["videos_scanned"] += 1

            replies = _fetch_recent_replies_to_own_comments(video_url, handle)
            if not replies:
                report["skipped_no_replies"] += 1
                continue

            for reply in replies:
                if report["candidates"] >= max_candidates:
                    break
                parent_text = (reply.get("parent_text") or "").strip()
                incoming_text = (reply.get("incoming_text") or "").strip()
                incoming_author = (reply.get("incoming_author") or "").strip()
                if not parent_text or not incoming_text:
                    continue

                # Use video_id + short parent-text hash as the dedupe key.
                # Two different parents on the same video → two different
                # thread_ids; same parent on the same video → same row,
                # dedup'd against already_touched.
                parent_hash = _short_text_hash(parent_text)
                thread_id = f"{video_id}::{parent_hash}"
                if thread_id in already_touched:
                    report["skipped_dedupe"] += 1
                    continue

                extras = {
                    "video_id": video_id,
                    "parent_text": parent_text[:600],
                    "incoming_text": incoming_text[:600],
                    "incoming_author": incoming_author[:80],
                    # Phase 2's content_agent reads `selftext_preview` to fill
                    # the "OP BODY" slot of the LLM draft prompt. For replies
                    # the "body" is the incoming message we're replying to.
                    "selftext_preview": incoming_text[:600],
                    # Anchor string for find_on_page when the reply is
                    # eventually posted. post_youtube_reply_via_servo will
                    # re-trim this with _trim_anchor on its own, but storing
                    # the full parent text in extras keeps the audit trail
                    # human-readable.
                    "anchor_hint": parent_text[:80],
                }
                audit_id = audit.log_candidate(
                    platform="youtube",
                    action="reply",
                    target_url=video_url,
                    target_thread_id=thread_id,
                    feature_hint="own_video_reply",
                    score=None,
                    extras=extras,
                )
                if audit_id is not None:
                    report["candidates"] += 1
                    already_touched.add(thread_id)
                    logger.info(
                        "recon: queued youtube reply candidate vid=%s author=%r",
                        video_id, incoming_author,
                    )

        return report


def scout_youtube_channel_comments(max_candidates: int = DEFAULT_CANDIDATES_PER_PASS) -> dict:
    """Emit a reply candidate for each new comment on the account's own
    YouTube videos that it has not answered.

    Read through the connected YouTube account (the one switched on for
    Outreach comments), so it needs that connection and no browser. The
    candidate's link names the comment (``&lc=``), which is where the reply
    is posted.
    """
    from backend.services.social_outreach import api_post

    report = {
        "platform": "youtube",
        "action": "reply",
        "comments_found": 0,
        "candidates": 0,
        "skipped_dedupe": 0,
        "reason": None,
    }
    if not kill_switch.is_enabled():
        report["reason"] = "kill_switch_off"
        return report
    blocked = web_access_block_reason("read the comments on your YouTube videos")
    if blocked:
        report["reason"] = "web_access_off"
        return report
    connection = api_post.account()
    if connection is None:
        report["reason"] = "no_youtube_account"
        return report

    from backend.services.connections.providers import youtube
    from backend.services.connections.service import build_ctx

    try:
        # Read more than asked for: some will already have a draft.
        comments = youtube.unanswered_comments(build_ctx(connection), limit=max_candidates * 4)
    except (RuntimeError, ValueError) as e:
        report["reason"] = "youtube_refused"
        report["detail"] = str(e)
        return report
    except Exception as e:  # noqa: BLE001 - a dropped connection is reported, not raised
        logger.warning("recon: reading channel comments failed: %s", e)
        report["reason"] = "youtube_unreachable"
        report["detail"] = type(e).__name__
        return report

    report["comments_found"] = len(comments)
    if not comments:
        report["reason"] = "no_new_comments"
        return report

    already_touched = set(audit.recent_thread_ids(
        "youtube", statuses=CANDIDATE_DEDUPE_STATUSES,
    ))
    for comment in comments:
        if report["candidates"] >= max_candidates:
            break
        thread_id = f"{comment['video_id']}::{comment['comment_id']}"
        if thread_id in already_touched:
            report["skipped_dedupe"] += 1
            continue
        text = comment["text"]
        audit_id = audit.log_candidate(
            platform="youtube",
            action="reply",
            target_url=(
                f"https://www.youtube.com/watch?v={comment['video_id']}&lc={comment['comment_id']}"
            ),
            target_thread_id=thread_id,
            feature_hint="own_video_reply",
            score=None,
            extras={
                "video_id": comment["video_id"],
                "title": comment["video_title"],
                # A comment on the video itself: there is no earlier comment of ours.
                "parent_text": "",
                "incoming_text": text[:600],
                "incoming_author": comment["author"][:80],
                "selftext_preview": text[:600],
                # The browser poster finds the comment to reply under by its opening words.
                "anchor_hint": text[:80],
            },
        )
        if audit_id is not None:
            report["candidates"] += 1
            already_touched.add(thread_id)
    return report


def enqueue_youtube_reply_candidate(
    video_url: str,
    parent_text: str,
    incoming_text: str,
    incoming_author: str = "",
) -> Optional[int]:
    """Hand-seed a YouTube reply candidate row, bypassing the (still-stubbed)
    scrape. Useful for testing the downstream draft/grade/dispatch pipeline
    or for one-off "please reply to this specific comment" hand-offs from
    the UI before the cron scout is fully wired.

    Returns the audit row id, or None on failure (kill switch off, dedupe
    hit, invalid URL, blank inputs).
    """
    if not kill_switch.is_enabled():
        return None
    video_id = _extract_youtube_video_id(video_url)
    if not video_id:
        return None
    parent_text = (parent_text or "").strip()
    incoming_text = (incoming_text or "").strip()
    if not parent_text or not incoming_text:
        return None
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    parent_hash = _short_text_hash(parent_text)
    thread_id = f"{video_id}::{parent_hash}"
    already_touched = set(audit.recent_thread_ids(
        "youtube", statuses=CANDIDATE_DEDUPE_STATUSES,
    ))
    if thread_id in already_touched:
        return None
    extras = {
        "video_id": video_id,
        "parent_text": parent_text[:600],
        "incoming_text": incoming_text[:600],
        "incoming_author": (incoming_author or "").strip()[:80],
        "selftext_preview": incoming_text[:600],
        "anchor_hint": parent_text[:80],
        "manual_seed": True,
    }
    return audit.log_candidate(
        platform="youtube",
        action="reply",
        target_url=canonical_url,
        target_thread_id=thread_id,
        feature_hint="own_video_reply",
        score=None,
        extras=extras,
    )


def _load_monitored_videos() -> list[str]:
    """Read youtube.monitored_videos from social_outreach_targets.json. Returns
    [] if the file or key is missing — caller treats that as a clean no-op.
    """
    import json
    import os
    from pathlib import Path
    root = os.environ.get("GUAARDVARK_ROOT") or str(Path(__file__).resolve().parents[3])
    targets_path = Path(root) / "data" / "agent" / "social_outreach_targets.json"
    try:
        with open(targets_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("could not load monitored videos: %s", e)
        return []
    yt = cfg.get("youtube") or {}
    vids = yt.get("monitored_videos") or []
    return [v for v in vids if isinstance(v, str) and v.strip()]


def _short_text_hash(text: str) -> str:
    """8-char hex hash of the first 200 chars of `text`. Stable across
    invocations so the dedupe set works across recon ticks.
    """
    import hashlib
    sample = (text or "").strip()[:200].encode("utf-8", errors="replace")
    return hashlib.sha1(sample).hexdigest()[:8]


def _fetch_recent_replies_to_own_comments(video_url: str, handle: str) -> list[dict]:
    """Return any new replies left under the account's comments on `video_url`.

    Each item: {"parent_text": str, "incoming_text": str, "incoming_author": str}.
    ``handle`` is the profile's account handle, the "@name" the comments carry.

    STUB — next pass: drive the agent to the video page, find each
    "@<handle>" comment, read the replies thread under it via the
    dom_metadata_extractor, diff against last-seen state to surface only
    new incoming replies. Until then this returns [] and the scout reports
    skipped_no_replies for every monitored video, which is a clean no-op
    rather than a noisy failure.

    The scaffolding around this function (log_candidate emission, dedupe,
    dispatcher routing) is fully working, so hand-seeding via
    enqueue_youtube_reply_candidate exercises the same downstream path
    we'll hit once this stub is replaced.
    """
    return []


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
