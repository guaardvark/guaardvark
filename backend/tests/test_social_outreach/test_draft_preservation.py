"""Servo failures must preserve the draft text in the audit row.

Background: under the supervised-mode marketing flow, the user clicks
'approve' in the UI to copy a grade-0.9 draft and post manually when
vision-driven autoposting fails. That requires the audit row to keep
its draft_text after a servo abort, not get wiped or replaced by a
text-less abort row.
"""
from unittest.mock import patch
import pytest

def test_mark_draft_aborted_preserves_draft_text(app):
    """A drafted row updated to 'aborted' must keep its draft_text."""
    from backend.services.social_outreach import audit
    from backend.models import SocialOutreachLog, db
    
    with app.app_context():
        row = SocialOutreachLog(
            platform="reddit",
            action="comment",
            status="drafted",
            draft_text="If you're running local LLMs, watch your VRAM allocation early.",
            target_url="https://reddit.com/r/x/comments/abc/y",
            target_thread_id="abc",
            grade_score=0.9,
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id
        
        # This function doesn't exist yet, so this test should fail to import or call
        ok = audit.mark_draft_aborted(rid, "servo: click_save_failed: timeout")
        assert ok is True
        
        updated = SocialOutreachLog.query.get(rid)
        assert updated.status == "aborted"
        assert updated.abort_reason == "servo: click_save_failed: timeout"
        # CRITICAL: draft_text preserved so the user can copy-paste it.
        assert updated.draft_text == "If you're running local LLMs, watch your VRAM allocation early."
        assert updated.grade_score == 0.9


def test_mark_draft_aborted_returns_false_for_missing_id(app):
    from backend.services.social_outreach import audit
    with app.app_context():
        # This function doesn't exist yet
        try:
            assert audit.mark_draft_aborted(999_999, "servo: x") is False
        except AttributeError:
            pytest.fail("mark_draft_aborted not implemented")


def test_run_one_pass_servo_fail_updates_existing_row_not_create_new(app):
    """A servo failure during run_one_pass updates the drafted row's status,
    rather than appending a separate abort row with empty text."""
    from unittest.mock import MagicMock, patch
    from backend.services.social_outreach.reddit_outreach import RedditOutreachLoop
    from backend.models import SocialOutreachLog, db
    
    with app.app_context(), \
         patch("backend.services.social_outreach.reddit_outreach.fetch_subreddit_rules", return_value=[]), \
         patch("backend.services.social_outreach.reddit_outreach.fetch_hot_threads") as mock_hot, \
         patch("backend.services.social_outreach.reddit_outreach.fetch_thread_comments", return_value=[]), \
         patch("backend.services.social_outreach.reddit_outreach.thread_is_relevant", return_value="test_hint"), \
         patch("backend.services.social_outreach.reddit_outreach.draft_via_backend") as mock_draft, \
         patch("backend.services.social_outreach.reddit_outreach.post_comment_via_servo", return_value=(False, "timeout")), \
         patch("backend.services.social_outreach.reddit_outreach.kill_switch.is_enabled", return_value=True):
        
        # Setup one thread
        mock_thread = MagicMock()
        mock_thread.id = "thread123"
        mock_thread.permalink = "https://reddit.com/r/test/comments/thread123"
        mock_hot.return_value = [mock_thread]
        
        # Create a real audit row for the draft to simulate draft_via_backend's behavior
        row = SocialOutreachLog(
            platform="reddit",
            action="comment",
            status="drafted",
            draft_text="Test draft text",
            target_url=mock_thread.permalink,
            target_thread_id=mock_thread.id,
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id
        
        mock_draft.return_value = {
            "audit_id": rid,
            "would_post": True,
            "draft": "Test draft text"
        }
        
        loop = RedditOutreachLoop()
        loop.run_one_pass("test_subreddit")
        
        # Verify only one row exists and it's aborted
        all_rows = SocialOutreachLog.query.all()
        assert len(all_rows) == 1
        assert all_rows[0].id == rid
        assert all_rows[0].status == "aborted"
        assert all_rows[0].abort_reason == "servo: timeout"
        assert all_rows[0].draft_text == "Test draft text"


def test_self_share_loop_servo_fail_updates_existing_row(app):
    """A servo failure during SelfShareLoop updates the drafted row's status."""
    from unittest.mock import MagicMock, patch
    from backend.services.social_outreach.self_share import SelfShareLoop
    from backend.models import SocialOutreachLog, db
    import json
    
    with app.app_context(), \
         patch("backend.services.social_outreach.self_share.fetch_subreddit_rules", return_value=[]), \
         patch("backend.services.social_outreach.self_share._draft_share") as mock_draft, \
         patch("backend.services.social_outreach.self_share._submit_post_via_servo", return_value=(False, "submit_timeout")), \
         patch("backend.services.social_outreach.self_share.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.self_share.kill_switch.cadence_allows_post", return_value=(True, None)):
        
        # Create a real audit row
        draft_content = json.dumps({"title": "Test Title", "body": "Test Body"})
        row = SocialOutreachLog(
            platform="reddit",
            action="share",
            status="drafted",
            draft_text=draft_content,
            target_url="https://reddit.com/r/test",
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id
        
        mock_draft.return_value = {
            "audit_id": rid,
            "would_post": True,
            "draft": draft_content
        }
        
        loop = SelfShareLoop()
        loop.run_one_pass("test_subreddit", "https://guaardvark.com")
        
        # Verify
        updated = SocialOutreachLog.query.get(rid)
        assert updated.status == "aborted"
        assert updated.abort_reason == "servo: submit_timeout"
        assert updated.draft_text == draft_content
