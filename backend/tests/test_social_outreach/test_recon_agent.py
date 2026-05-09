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
