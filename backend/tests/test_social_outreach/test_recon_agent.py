"""Recon agent (Phase 1) — emits candidates, never drafts, never posts.

The whole point of the recon split is that this phase has zero posting risk,
so the tests focus on:
  • candidate rows have the right shape
  • dedupe walks candidate/drafted/approved/posted (not just posted)
  • kill-switch off short-circuits the pass cleanly
  • banned subs short-circuit before fetching threads
  • max_candidates is respected
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.models import SocialOutreachLog, db
from backend.services.social_outreach.recon import (
    CANDIDATE_DEDUPE_STATUSES,
    RecondAgent,
)
from backend.services.social_outreach.reddit_outreach import RedditThread


@pytest.fixture(autouse=True)
def mock_relevance_grader():
    """Default-mock the LLM relevance scorer to skipped=True so existing tests
    don't depend on Ollama. Tests that care about the gate override locally."""
    with patch(
        "backend.services.social_outreach.recon.external_grader.score_thread_relevance",
        return_value={"grade": 0.0, "skipped": True, "model": None, "reason": "test_default"},
    ):
        yield


@pytest.fixture
def app():
    """Flask app with in-memory database."""
    from flask import Flask
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _thread(id_: str, title: str, score: int = 100) -> RedditThread:
    return RedditThread(
        id=id_,
        url=f"https://www.reddit.com/r/LocalLLaMA/comments/{id_}/x/",
        permalink=f"https://www.reddit.com/r/LocalLLaMA/comments/{id_}/x/",
        subreddit="LocalLLaMA",
        title=title,
        selftext="",
        score=score,
        num_comments=10,
        created_utc=0.0,
    )


def test_scout_reddit_short_circuits_when_kill_switch_off(app):
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=False):
        report = RecondAgent().scout_reddit("LocalLLaMA")
        assert report["reason"] == "kill_switch_off"
        assert report["candidates"] == 0
        assert SocialOutreachLog.query.count() == 0  # no rows written


def test_scout_reddit_short_circuits_when_sub_bans_self_promo(app):
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.services.social_outreach.recon.fetch_subreddit_rules",
                  return_value=["No self-promotion of any kind"]), \
            patch("backend.services.social_outreach.recon.fetch_hot_threads") as fetch_hot:
        report = RecondAgent().scout_reddit("BannedSub")
        assert report["reason"] is not None
        assert report["reason"].startswith("sub_bans_self_promo")
        # We bail before fetching threads so the network call never happens
        fetch_hot.assert_not_called()


def test_scout_reddit_no_hot_threads(app):
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.services.social_outreach.recon.fetch_subreddit_rules", return_value=[]), \
            patch("backend.services.social_outreach.recon.fetch_hot_threads", return_value=[]):
        report = RecondAgent().scout_reddit("LocalLLaMA")
        assert report["reason"] == "no_hot_threads"
        assert report["candidates"] == 0


def test_scout_reddit_emits_candidate_with_expected_fields(app):
    """A relevant thread becomes a status=candidate row with feature_hint encoded
    in draft_text (JSON) and grade_score = normalized score."""
    thread = _thread("abc123", "Anyone tried Ollama with local RAG?", score=500)
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.services.social_outreach.recon.fetch_subreddit_rules", return_value=[]), \
            patch("backend.services.social_outreach.recon.fetch_hot_threads", return_value=[thread]), \
            patch("backend.services.social_outreach.recon.fetch_thread_comments", return_value=[]), \
            patch("backend.services.social_outreach.recon.thread_is_relevant", return_value="ollama_rag"):
        report = RecondAgent().scout_reddit("LocalLLaMA")
        assert report["candidates"] == 1
        rows = SocialOutreachLog.query.all()
        assert len(rows) == 1
        row = rows[0]
        assert row.platform == "reddit"
        assert row.action == "comment"
        assert row.status == "candidate"
        assert row.target_thread_id == "abc123"
        # grade_score = min(1.0, score/1000) = 500/1000 = 0.5
        assert row.grade_score == pytest.approx(0.5)
        # feature_hint is encoded in draft_text JSON during the candidate stage
        import json
        payload = json.loads(row.draft_text)
        assert payload["feature_hint"] == "ollama_rag"
        assert payload["stage"] == "recon"
        assert payload["title"] == "Anyone tried Ollama with local RAG?"


def test_scout_reddit_skips_irrelevant_threads(app):
    threads = [_thread("a", "weather forecast"), _thread("b", "cat picture")]
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.services.social_outreach.recon.fetch_subreddit_rules", return_value=[]), \
            patch("backend.services.social_outreach.recon.fetch_hot_threads", return_value=threads), \
            patch("backend.services.social_outreach.recon.fetch_thread_comments", return_value=[]), \
            patch("backend.services.social_outreach.recon.thread_is_relevant", return_value=None):
        report = RecondAgent().scout_reddit("LocalLLaMA")
        assert report["candidates"] == 0
        assert report["skipped_irrelevant"] == 2


def test_scout_reddit_dedupes_against_existing_candidate_rows(app):
    """A thread already at status=candidate should NOT be re-emitted on the
    next pass — that's the whole reason CANDIDATE_DEDUPE_STATUSES exists."""
    thread = _thread("dup1", "Local LLM benchmarks?", score=200)
    with app.app_context():
        existing = SocialOutreachLog(
            platform="reddit",
            action="comment",
            target_thread_id="dup1",
            status="candidate",
            draft_text='{"feature_hint": "stub", "stage": "recon"}',
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.session.add(existing)
        db.session.commit()

        with patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
                patch("backend.services.social_outreach.recon.fetch_subreddit_rules", return_value=[]), \
                patch("backend.services.social_outreach.recon.fetch_hot_threads", return_value=[thread]), \
                patch("backend.services.social_outreach.recon.fetch_thread_comments", return_value=[]), \
                patch("backend.services.social_outreach.recon.thread_is_relevant", return_value="local_llm"):
            report = RecondAgent().scout_reddit("LocalLLaMA")
        assert report["candidates"] == 0
        assert report["skipped_dedupe"] == 1
        # Only the original row exists; no second one was added.
        assert SocialOutreachLog.query.count() == 1


def test_scout_reddit_respects_max_candidates(app):
    """Three relevant threads, max=2 → only 2 candidate rows emitted."""
    threads = [_thread(f"id{i}", f"Topic {i}", score=300 + i) for i in range(3)]
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.services.social_outreach.recon.fetch_subreddit_rules", return_value=[]), \
            patch("backend.services.social_outreach.recon.fetch_hot_threads", return_value=threads), \
            patch("backend.services.social_outreach.recon.fetch_thread_comments", return_value=[]), \
            patch("backend.services.social_outreach.recon.thread_is_relevant", return_value="x"):
        report = RecondAgent().scout_reddit("LocalLLaMA", max_candidates=2)
        assert report["candidates"] == 2
        assert SocialOutreachLog.query.count() == 2


def test_scout_reddit_skips_by_llm_when_relevance_grade_low(app):
    """LLM relevance judge says no → row is NOT emitted, skipped_by_llm++."""
    thread = _thread("hostile1", "Why I hate local LLMs", score=400)
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.services.social_outreach.recon.fetch_subreddit_rules", return_value=[]), \
            patch("backend.services.social_outreach.recon.fetch_hot_threads", return_value=[thread]), \
            patch("backend.services.social_outreach.recon.fetch_thread_comments", return_value=["awful experience"]), \
            patch("backend.services.social_outreach.recon.thread_is_relevant", return_value="local_llm"), \
            patch(
                "backend.services.social_outreach.recon.external_grader.score_thread_relevance",
                return_value={"grade": 0.2, "skipped": False, "verdict": "skip", "reason": "OP is venting"},
            ):
        report = RecondAgent().scout_reddit("LocalLLaMA")
        assert report["candidates"] == 0
        assert report["skipped_by_llm"] == 1
        assert SocialOutreachLog.query.count() == 0


def test_scout_reddit_passes_when_llm_relevance_grade_high(app):
    """LLM grades ≥ MIN_RELEVANCE_GRADE → emit candidate."""
    thread = _thread("good1", "How do you keep VRAM under control with Qwen?", score=200)
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.services.social_outreach.recon.fetch_subreddit_rules", return_value=[]), \
            patch("backend.services.social_outreach.recon.fetch_hot_threads", return_value=[thread]), \
            patch("backend.services.social_outreach.recon.fetch_thread_comments", return_value=[]), \
            patch("backend.services.social_outreach.recon.thread_is_relevant", return_value="vram"), \
            patch(
                "backend.services.social_outreach.recon.external_grader.score_thread_relevance",
                return_value={"grade": 0.85, "skipped": False, "verdict": "good_fit", "reason": "asking for advice"},
            ):
        report = RecondAgent().scout_reddit("LocalLLaMA")
        assert report["candidates"] == 1
        assert report["skipped_by_llm"] == 0
        # Relevance grade is preserved in the JSON payload
        import json
        row = SocialOutreachLog.query.first()
        payload = json.loads(row.draft_text)
        assert payload["relevance_grade"] == 0.85
        assert "good_fit" not in payload  # verdict isn't kept, only grade + reason
        assert payload["relevance_reason"].startswith("asking")


def test_scout_reddit_treats_grader_skipped_as_pass(app):
    """If the relevance grader is unavailable (model not loaded), don't block.
    Emit the candidate based on keyword match alone — same behavior as before
    the LLM gate existed."""
    thread = _thread("nomodel1", "Local AI thoughts", score=100)
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.services.social_outreach.recon.fetch_subreddit_rules", return_value=[]), \
            patch("backend.services.social_outreach.recon.fetch_hot_threads", return_value=[thread]), \
            patch("backend.services.social_outreach.recon.fetch_thread_comments", return_value=[]), \
            patch("backend.services.social_outreach.recon.thread_is_relevant", return_value="local_ai"), \
            patch(
                "backend.services.social_outreach.recon.external_grader.score_thread_relevance",
                return_value={"grade": 0.0, "skipped": True, "model": None, "reason": "no_grader_model_loaded"},
            ):
        report = RecondAgent().scout_reddit("LocalLLaMA")
        assert report["candidates"] == 1
        assert report["skipped_by_llm"] == 0


def test_dedupe_includes_drafted_and_posted_not_aborted(app):
    """Sanity-check the constant — drafted/approved/posted dedupe in,
    aborted/rejected don't (they're dead-ends and may be retryable)."""
    assert "candidate" in CANDIDATE_DEDUPE_STATUSES
    assert "drafted" in CANDIDATE_DEDUPE_STATUSES
    assert "approved" in CANDIDATE_DEDUPE_STATUSES
    assert "posted" in CANDIDATE_DEDUPE_STATUSES
    assert "aborted" not in CANDIDATE_DEDUPE_STATUSES
    assert "rejected" not in CANDIDATE_DEDUPE_STATUSES


# --- YouTube recon (slice 6) ----------------------------------------------
# Same shape as the scout_reddit suite above — kill switch, dedupe, candidate
# emission, LLM gate. Plus YouTube-specific filtering: DDG returns more than
# just video pages and we only want /watch?v= URLs.

from backend.services.social_outreach.recon import _extract_youtube_video_id


def _ddg_result(title: str, url: str, snippet: str = "") -> dict:
    """Shape enhanced_web_search returns under data.results[]."""
    return {"title": title, "url": url, "snippet": snippet}


def _ddg_response(*results: dict) -> dict:
    """Wrap raw results in the {success, data:{results: [...]}} envelope
    enhanced_web_search emits on a successful DuckDuckGo pass."""
    return {
        "success": True,
        "strategy_used": "duckduckgo_search",
        "data": {"type": "search_results", "results": list(results)},
    }


def test_extract_video_id_handles_common_url_shapes():
    assert _extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_youtube_video_id("https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=30s") == "dQw4w9WgXcQ"
    assert _extract_youtube_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    # Shorts ARE commentable like regular videos; the regex must match them.
    assert _extract_youtube_video_id("https://www.youtube.com/shorts/EcOPdqe2GDM") == "EcOPdqe2GDM"
    # Live archives are full videos with comment threads — also commentable.
    assert _extract_youtube_video_id("https://www.youtube.com/live/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    # Channel pages, search, playlists — not commentable as a single video.
    assert _extract_youtube_video_id("https://www.youtube.com/@LocalLLaMA") is None
    assert _extract_youtube_video_id("https://www.youtube.com/results?search_query=ollama") is None
    assert _extract_youtube_video_id("https://www.youtube.com/playlist?list=PLxyz") is None
    assert _extract_youtube_video_id("") is None
    assert _extract_youtube_video_id("not a url at all") is None


def test_scout_youtube_short_circuits_when_kill_switch_off(app):
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=False):
        report = RecondAgent().scout_youtube("ComfyUI tutorial")
        assert report["reason"] == "kill_switch_off"
        assert report["candidates"] == 0
        assert SocialOutreachLog.query.count() == 0


def test_scout_youtube_handles_web_search_failure(app):
    """An exception out of enhanced_web_search shouldn't tear down the pass —
    same skip-with-reason pattern as the relevance grader."""
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", side_effect=RuntimeError("ddg down")):
        report = RecondAgent().scout_youtube("ComfyUI tutorial")
        assert report["reason"] is not None
        assert "web_search_failed" in report["reason"]
        assert report["candidates"] == 0


def test_scout_youtube_handles_no_results(app):
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch(
                "backend.api.web_search_api.enhanced_web_search",
                return_value={"success": True, "data": {"results": []}},
            ):
        report = RecondAgent().scout_youtube("ComfyUI tutorial")
        assert report["reason"] == "web_search_empty_results"
        assert report["candidates"] == 0


def test_scout_youtube_emits_candidate_with_expected_fields(app):
    """A relevant video → status=candidate row; feature_hint encoded in
    draft_text JSON; rank-decay score in grade_score."""
    response = _ddg_response(
        _ddg_result(
            title="Ollama tutorial — run local LLMs in 10 minutes",
            url="https://www.youtube.com/watch?v=AGAETsxjg0o",
            snippet="Learn how to run large language models locally with Ollama on Linux, Mac, and Windows.",
        ),
    )
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", return_value=response):
        report = RecondAgent().scout_youtube("Ollama local LLM")
        assert report["candidates"] == 1
        rows = SocialOutreachLog.query.all()
        assert len(rows) == 1
        row = rows[0]
        assert row.platform == "youtube"
        assert row.action == "comment"
        assert row.status == "candidate"
        assert row.target_thread_id == "AGAETsxjg0o"
        # target_url is reconstructed from the canonical /watch?v= shape, NOT
        # whatever DDG returned — protects against XSS injection if a
        # compromised search response surfaced a javascript:/data: URL.
        assert row.target_url == "https://www.youtube.com/watch?v=AGAETsxjg0o"
        # Single result → rank-decay score = 1.0 - 0/1 = 1.0
        assert row.grade_score == pytest.approx(1.0)
        import json
        payload = json.loads(row.draft_text)
        assert payload["feature_hint"] == "local_ai"  # "ollama" matches RELEVANCE_KEYWORDS
        assert payload["stage"] == "recon"
        assert payload["video_id"] == "AGAETsxjg0o"
        assert "Ollama" in payload["title"]
        assert payload["search_query"].startswith("site:youtube.com ")
        assert payload["rank"] == 0
        # Phase 2 (ContentAgent) reads `selftext_preview` to fill the OP BODY
        # slot of the LLM draft prompt. Without this alias the YouTube draft
        # would lose the snippet entirely — verify it's there.
        assert payload["selftext_preview"] == payload["snippet"]


def test_scout_youtube_skips_non_video_urls(app):
    """DDG sometimes returns channels, playlists, or the search-results page
    itself. Those don't have a video id so they don't become candidates."""
    response = _ddg_response(
        _ddg_result(
            title="LocalLLaMA channel",
            url="https://www.youtube.com/@LocalLLaMA",
            snippet="Channel page",
        ),
        _ddg_result(
            title="Search results: ollama",
            url="https://www.youtube.com/results?search_query=ollama",
            snippet="Search page",
        ),
        _ddg_result(
            title="Playlist: best of local AI",
            url="https://www.youtube.com/playlist?list=PLxyz",
            snippet="Playlist page",
        ),
    )
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", return_value=response):
        report = RecondAgent().scout_youtube("Ollama local LLM")
        assert report["candidates"] == 0
        assert report["skipped_non_video"] == 3
        assert SocialOutreachLog.query.count() == 0


def test_scout_youtube_skips_irrelevant_videos(app):
    """A video URL that doesn't match any keyword profile should not become
    a candidate — keyword filter runs after the URL filter."""
    response = _ddg_response(
        _ddg_result(
            title="cute kittens compilation",
            url="https://www.youtube.com/watch?v=AAAAAAAAAAA",
            snippet="cats and kittens being adorable",
        ),
    )
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", return_value=response):
        report = RecondAgent().scout_youtube("cat content")
        assert report["candidates"] == 0
        assert report["skipped_irrelevant"] == 1


def test_scout_youtube_dedupes_against_existing_rows(app):
    """A video already at status=candidate should not be re-emitted on the
    next pass — same dedupe semantics as scout_reddit."""
    response = _ddg_response(
        _ddg_result(
            title="Ollama tutorial",
            url="https://www.youtube.com/watch?v=DUPLICATEID",
            snippet="local llm setup",
        ),
    )
    with app.app_context():
        existing = SocialOutreachLog(
            platform="youtube",
            action="comment",
            target_thread_id="DUPLICATEID",
            status="candidate",
            draft_text='{"feature_hint": "local_ai", "stage": "recon"}',
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.session.add(existing)
        db.session.commit()

        with patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
                patch("backend.api.web_search_api.enhanced_web_search", return_value=response):
            report = RecondAgent().scout_youtube("Ollama local LLM")
        assert report["candidates"] == 0
        assert report["skipped_dedupe"] == 1
        # Only the original row exists; no second one was added.
        assert SocialOutreachLog.query.count() == 1


def test_scout_youtube_respects_max_candidates(app):
    """Three relevant video results, max=2 → only 2 candidate rows emitted."""
    response = _ddg_response(
        *[
            _ddg_result(
                title=f"Ollama tutorial #{i}",
                url=f"https://www.youtube.com/watch?v=VID{i:08d}AB"[:43],
                snippet="local llm",
            )
            for i in range(3)
        ]
    )
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", return_value=response):
        report = RecondAgent().scout_youtube("Ollama local LLM", max_candidates=2)
        assert report["candidates"] == 2
        assert SocialOutreachLog.query.count() == 2


def test_scout_youtube_skips_by_llm_when_relevance_grade_low(app):
    """Override the autouse skipped-grader fixture with a hard "skip" verdict
    and confirm the row is not emitted."""
    response = _ddg_response(
        _ddg_result(
            title="Why Ollama is broken trash",
            url="https://www.youtube.com/watch?v=HOSTILEVID0",
            snippet="rant about local LLM problems",
        ),
    )
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", return_value=response), \
            patch(
                "backend.services.social_outreach.recon.external_grader.score_thread_relevance",
                return_value={"grade": 0.2, "skipped": False, "verdict": "skip", "reason": "video is venting"},
            ):
        report = RecondAgent().scout_youtube("Ollama local LLM")
        assert report["candidates"] == 0
        assert report["skipped_by_llm"] == 1
        assert SocialOutreachLog.query.count() == 0


def test_scout_youtube_treats_grader_skipped_as_pass(app):
    """If the relevance grader is unavailable (model not loaded), don't block —
    emit the candidate based on keyword match alone."""
    response = _ddg_response(
        _ddg_result(
            title="Local LLM with Ollama",
            url="https://www.youtube.com/watch?v=NOMODELVID0",
            snippet="quick walkthrough",
        ),
    )
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", return_value=response), \
            patch(
                "backend.services.social_outreach.recon.external_grader.score_thread_relevance",
                return_value={"grade": 0.0, "skipped": True, "model": None, "reason": "no_grader_model_loaded"},
            ):
        report = RecondAgent().scout_youtube("Ollama local LLM")
        assert report["candidates"] == 1
        assert report["skipped_by_llm"] == 0


def test_tick_recon_youtube_no_targets_returns_skipped():
    """Beat tick with empty keyword_profiles returns skipped without raising."""
    from backend.tasks.social_outreach_tasks import tick_recon_youtube
    with patch("backend.tasks.social_outreach_tasks._load_targets", return_value={}):
        result = tick_recon_youtube.run()
        assert result == {"skipped": True, "reason": "no_targets"}


def test_tick_recon_youtube_round_robins_profiles(app):
    """Successive ticks should scan different keyword profiles, not the same
    one. We patch _next_target to verify it's called with the youtube_recon
    key + the configured profile list."""
    from backend.tasks.social_outreach_tasks import tick_recon_youtube
    profiles = ["ComfyUI tutorial", "Ollama local LLM"]
    with patch("backend.tasks.social_outreach_tasks._load_targets",
               return_value={"youtube": {"keyword_profiles": profiles}}), \
            patch("backend.tasks.social_outreach_tasks._next_target",
                  return_value="ComfyUI tutorial") as next_target, \
            patch("backend.tasks.social_outreach_tasks._with_app_context",
                  return_value={"platform": "youtube", "candidates": 0}):
        result = tick_recon_youtube.run()
        next_target.assert_called_once_with("youtube_recon", profiles)
        assert result["platform"] == "youtube"


def test_scout_youtube_in_pass_dedupe_same_video_two_url_shapes(app):
    """DDG can return the same video under two URL shapes (e.g. youtu.be vs
    youtube.com/watch?v=) in a single response. Both map to the same video_id
    but neither is in audit yet — without an in-pass dedupe set, both would
    emit candidate rows. Slice-6 review caught this."""
    response = _ddg_response(
        _ddg_result(
            title="Ollama tutorial — long form",
            url="https://www.youtube.com/watch?v=DOUBLECOUNT",
            snippet="local llm walkthrough",
        ),
        _ddg_result(
            title="Ollama tutorial — short link",
            url="https://youtu.be/DOUBLECOUNT",
            snippet="same video, different shape",
        ),
    )
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", return_value=response):
        report = RecondAgent().scout_youtube("Ollama local LLM")
        assert report["candidates"] == 1
        assert report["skipped_dedupe"] == 1
        # Only one row written, even though both DDG results pass the keyword filter.
        assert SocialOutreachLog.query.count() == 1


def test_scout_youtube_handles_non_dict_search_response(app):
    """If web_search returns something pathological (string, list), the .get()
    chain shouldn't crash the celery task. Skip-with-reason instead."""
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", return_value="garbage"):
        report = RecondAgent().scout_youtube("Ollama local LLM")
        assert report["reason"] == "web_search_no_results"
        assert report["candidates"] == 0


def test_scout_youtube_target_url_is_canonical_not_ddg_string(app):
    """Even if DDG returns a tracking-decorated or otherwise-modified URL,
    the row's target_url is rebuilt from the regex-validated video_id —
    closes the XSS surface where a malicious DDG response would land in
    target_url and later flow into a UI <a href>."""
    response = _ddg_response(
        _ddg_result(
            title="Ollama tutorial",
            url="https://www.youtube.com/watch?v=CANONICAL00&utm_source=evil&list=spam",
            snippet="local llm",
        ),
    )
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", return_value=response):
        RecondAgent().scout_youtube("Ollama local LLM")
        row = SocialOutreachLog.query.first()
        assert row is not None
        assert row.target_url == "https://www.youtube.com/watch?v=CANONICAL00"


def test_content_agent_drafts_youtube_candidate_without_crashing(app):
    """End-to-end: scout_youtube emits a candidate, ContentAgent.draft_candidate
    should be able to process it. Catches schema mismatch between recon
    payload and Phase 2's _build_thread_context. (Slice-6 review BLOCKER.)"""
    response = _ddg_response(
        _ddg_result(
            title="Ollama tutorial — run local LLMs",
            url="https://www.youtube.com/watch?v=E2EYTVID0AB",
            snippet="A walkthrough of installing Ollama and running local LLMs on Linux.",
        ),
    )
    with app.app_context(), \
            patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.api.web_search_api.enhanced_web_search", return_value=response):
        RecondAgent().scout_youtube("Ollama local LLM")

        row = SocialOutreachLog.query.first()
        assert row is not None
        assert row.platform == "youtube"
        assert row.status == "candidate"

        # Drive Phase 2 with the LLM mocked — we're verifying schema, not
        # actual draft quality. The draft_outreach_text result needs to look
        # like a real LLM response for the row to transition to "drafted".
        from backend.services.social_outreach.content_agent import ContentAgent
        with patch(
                "backend.services.social_outreach.content_agent.persona.draft_outreach_text",
                return_value={"draft": "Quick note — Ollama works great for this.", "grade": 0.85, "reason": "on-topic, short"},
            ), patch(
                "backend.services.social_outreach.content_agent.external_grader.grade_draft_externally",
                return_value={"grade": 0.0, "skipped": True, "reason": "no_grader_model_loaded"},
            ):
            result = ContentAgent().draft_candidate(row.id)
        # The exact status depends on grade thresholds; what we care about is
        # that the call did NOT raise (the BLOCKER would surface as a crash
        # or a JSON-decode failure when _build_thread_context reads the YT
        # payload). Status should be "drafted" or "rejected", not "missing".
        assert result["status"] in ("drafted", "rejected")
        # The selftext_preview alias should have flowed through to the
        # thread_context that the persona drafter saw — which is the whole
        # point of the alias fix.
        from backend.services.social_outreach.content_agent import _build_thread_context
        import json
        payload = json.loads(row.draft_text) if row.status == "candidate" else {}
        if payload:
            ctx = _build_thread_context(payload)
            assert "walkthrough of installing Ollama" in ctx
