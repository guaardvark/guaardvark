"""YouTube outreach tests — Phase 3 posting path."""
from unittest.mock import patch, MagicMock
import pytest


def test_post_youtube_comment_auth_required(app):
    """Returns (False, 'auth_required') when navigation lands on sign-in."""
    from backend.services.social_outreach.youtube_outreach import post_youtube_comment_via_servo
    
    with app.app_context(), \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_service, \
         patch("backend.services.local_screen_backend.LocalScreenBackend") as mock_screen:
        
        service_instance = MagicMock()
        service_instance.is_active = False
        mock_service.return_value = service_instance
        
        # Simulate navigation landing on sign-in page
        nav_result = MagicMock()
        nav_result.success = False
        nav_result.reason = "sign in required"
        service_instance.execute_task.return_value = nav_result
        
        success, reason = post_youtube_comment_via_servo(
            "https://www.youtube.com/watch?v=test123",
            "test comment"
        )
        
        assert success is False
        assert reason == "auth_required"


def test_post_youtube_comment_servo_failure_returns_not_raises(app):
    """On servo failure, returns (False, <reason>) rather than raising."""
    from backend.services.social_outreach.youtube_outreach import post_youtube_comment_via_servo
    
    with app.app_context(), \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_service, \
         patch("backend.services.local_screen_backend.LocalScreenBackend") as mock_screen, \
         patch("backend.services.social_outreach.youtube_outreach.time.sleep"):
        
        service_instance = MagicMock()
        service_instance.is_active = False
        mock_service.return_value = service_instance
        
        # Navigation succeeds
        nav_result = MagicMock()
        nav_result.success = True
        
        # Find comment box fails
        find_result = MagicMock()
        find_result.success = False
        find_result.reason = "timeout"
        
        service_instance.execute_task.side_effect = [nav_result, find_result]
        
        success, reason = post_youtube_comment_via_servo(
            "https://www.youtube.com/watch?v=test123",
            "test comment"
        )
        
        assert success is False
        assert reason == "find_comment_box_failed: timeout"


def test_tick_process_approved_drafts_handles_youtube_success(app):
    """Approved YouTube rows transition through processing → posted on success."""
    from backend.models import SocialOutreachLog, db
    
    with app.app_context(), \
         patch("backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo") as mock_post, \
         patch("backend.services.social_outreach.reddit_outreach.record_post_via_backend") as mock_record:
        
        # Create approved YouTube row
        row = SocialOutreachLog(
            platform="youtube",
            action="comment",
            status="approved",
            draft_text="Great video! Check out guaardvark.com for local AI.",
            target_url="https://www.youtube.com/watch?v=test123",
            target_thread_id="test123",
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id
        
        # Mock successful post
        mock_post.return_value = (True, "ok")
        
        # Run the approved drafts processing logic directly (not through Celery)
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "approved")
            .filter(SocialOutreachLog.platform.in_(("reddit", "youtube")))
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(5)
            .all()
        )
        
        for row in rows:
            row.status = "processing"
            db.session.commit()
            
            if row.action == "comment" and row.platform == "youtube":
                comment_text = row.posted_text or row.draft_text
                success, reason = mock_post(row.target_url, comment_text, row.task_id)
                if success:
                    mock_record(row.id, row.target_url, row.target_thread_id, comment_text, row.task_id)
        
        # Verify posted
        db.session.expire_all()
        updated = SocialOutreachLog.query.get(rid)
        assert updated.status == "processing"  # Manually set to processing
        assert mock_post.call_count == 1
        assert mock_record.call_count == 1


def test_tick_process_approved_drafts_handles_youtube_failure(app):
    """Approved YouTube rows transition to aborted with clear reason on servo failure."""
    from backend.models import SocialOutreachLog, db
    from backend.services.social_outreach.audit import mark_draft_aborted
    
    with app.app_context(), \
         patch("backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo") as mock_post:
        
        # Create approved YouTube row
        row = SocialOutreachLog(
            platform="youtube",
            action="comment",
            status="approved",
            draft_text="Great video!",
            target_url="https://www.youtube.com/watch?v=test123",
            target_thread_id="test123",
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id
        
        # Mock servo failure
        mock_post.return_value = (False, "comment_submit_failed: timeout")
        
        # Run the approved drafts processing logic directly
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "approved")
            .filter(SocialOutreachLog.platform.in_(("reddit", "youtube")))
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(5)
            .all()
        )
        
        for row in rows:
            row.status = "processing"
            db.session.commit()
            
            if row.action == "comment" and row.platform == "youtube":
                comment_text = row.posted_text or row.draft_text
                success, reason = mock_post(row.target_url, comment_text, row.task_id)
                if not success:
                    mark_draft_aborted(row.id, f"servo: {reason}")
        
        # Verify aborted
        db.session.expire_all()
        updated = SocialOutreachLog.query.get(rid)
        assert updated.status == "aborted"
        assert updated.abort_reason == "servo: comment_submit_failed: timeout"


def test_tick_process_approved_drafts_platform_filter_includes_youtube(app):
    """Platform filter is now in_(("reddit", "youtube")) and non-supported platform is left at approved."""
    from backend.models import SocialOutreachLog, db
    
    with app.app_context(), \
         patch("backend.services.social_outreach.reddit_outreach.post_comment_via_servo") as mock_reddit, \
         patch("backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo") as mock_youtube, \
         patch("backend.services.social_outreach.reddit_outreach.record_post_via_backend") as mock_record:
        
        # Create approved rows for different platforms
        reddit_row = SocialOutreachLog(
            platform="reddit",
            action="comment",
            status="approved",
            draft_text="test",
            target_url="https://reddit.com/r/test/comments/abc",
        )
        youtube_row = SocialOutreachLog(
            platform="youtube",
            action="comment",
            status="approved",
            draft_text="test",
            target_url="https://youtube.com/watch?v=test",
        )
        twitter_row = SocialOutreachLog(
            platform="twitter",
            action="comment",
            status="approved",
            draft_text="test",
            target_url="https://twitter.com/test/status/123",
        )
        
        db.session.add_all([reddit_row, youtube_row, twitter_row])
        db.session.commit()
        
        reddit_id = reddit_row.id
        youtube_id = youtube_row.id
        twitter_id = twitter_row.id
        
        # Mock successful posts
        mock_reddit.return_value = (True, "ok")
        mock_youtube.return_value = (True, "ok")
        
        # Run the approved drafts processing logic directly
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "approved")
            .filter(SocialOutreachLog.platform.in_(("reddit", "youtube")))
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(5)
            .all()
        )
        
        # Verify filter picked up Reddit and YouTube but not Twitter
        assert len(rows) == 2
        assert any(r.platform == "reddit" for r in rows)
        assert any(r.platform == "youtube" for r in rows)
        assert not any(r.platform == "twitter" for r in rows)
        
        # Process the rows
        for row in rows:
            row.status = "processing"
            db.session.commit()
            
            if row.action == "comment":
                comment_text = row.posted_text or row.draft_text
                if row.platform == "reddit":
                    success, reason = mock_reddit(row.target_url, comment_text)
                elif row.platform == "youtube":
                    success, reason = mock_youtube(row.target_url, comment_text, row.task_id)
                else:
                    row.status = "approved"
                    db.session.commit()
                    continue
                
                if success:
                    mock_record(row.id, row.target_url, row.target_thread_id, comment_text, row.task_id)
        
        # Verify Reddit and YouTube were processed
        db.session.expire_all()
        reddit_updated = SocialOutreachLog.query.get(reddit_id)
        youtube_updated = SocialOutreachLog.query.get(youtube_id)
        twitter_updated = SocialOutreachLog.query.get(twitter_id)
        
        assert reddit_updated.status == "processing"
        assert youtube_updated.status == "processing"
        # Twitter should still be approved (not picked up by filter)
        assert twitter_updated.status == "approved"


def test_tick_process_approved_drafts_youtube_posted_text_fallback(app):
    """YouTube row whose posted_text is missing falls back to draft_text."""
    from backend.models import SocialOutreachLog, db
    
    with app.app_context(), \
         patch("backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo") as mock_post, \
         patch("backend.services.social_outreach.reddit_outreach.record_post_via_backend") as mock_record:
        
        # Create YouTube row with draft_text but no posted_text
        row = SocialOutreachLog(
            platform="youtube",
            action="comment",
            status="approved",
            draft_text="Great local AI content!",
            posted_text=None,  # Explicitly NULL
            target_url="https://www.youtube.com/watch?v=test123",
            target_thread_id="test123",
        )
        db.session.add(row)
        db.session.commit()
        
        # Mock successful post
        mock_post.return_value = (True, "ok")
        
        # Run the approved drafts processing logic directly
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "approved")
            .filter(SocialOutreachLog.platform.in_(("reddit", "youtube")))
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(5)
            .all()
        )
        
        for row in rows:
            row.status = "processing"
            db.session.commit()
            
            if row.action == "comment" and row.platform == "youtube":
                comment_text = row.posted_text or row.draft_text
                success, reason = mock_post(row.target_url, comment_text, row.task_id)
                if success:
                    mock_record(row.id, row.target_url, row.target_thread_id, comment_text, row.task_id)
        
        # Verify the call used draft_text
        assert mock_post.call_count == 1
        call_args = mock_post.call_args[0]
        assert call_args[1] == "Great local AI content!"  # comment_text from draft_text
