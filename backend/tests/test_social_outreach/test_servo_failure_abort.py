"""After SERVO_FAILURE_ABORT_THRESHOLD failures in one pass, the loop aborts."""
from unittest.mock import patch, MagicMock
from backend.services.social_outreach import reddit_outreach
from backend.services.social_outreach.kill_switch import SERVO_FAILURE_ABORT_THRESHOLD


def test_loop_aborts_after_consecutive_servo_failures():
    """If post_comment_via_servo returns (False, ...) multiple times, the loop must abort
    rather than continuing through more threads."""
    
    # Create 5 fake threads (more than SERVO_FAILURE_ABORT_THRESHOLD)
    fake_threads = [
        reddit_outreach.RedditThread(
            id=f"thread{i}",
            url=f"https://reddit.com/r/test/comments/{i}",
            permalink=f"/r/test/comments/{i}",
            subreddit="test",
            title=f"Test {i}",
            selftext="body",
            score=100,
            num_comments=10,
            created_utc=1234567890.0,
        )
        for i in range(5)
    ]
    
    with patch("backend.services.social_outreach.reddit_outreach.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.reddit_outreach.fetch_subreddit_rules", return_value=[]), \
         patch("backend.services.social_outreach.reddit_outreach.fetch_hot_threads", return_value=fake_threads), \
         patch("backend.services.social_outreach.reddit_outreach.audit.recent_thread_ids", return_value=set()), \
         patch("backend.services.social_outreach.reddit_outreach.fetch_thread_comments", return_value=["ollama comment"]), \
         patch("backend.services.social_outreach.reddit_outreach.draft_via_backend") as mock_draft, \
         patch("backend.services.social_outreach.reddit_outreach.post_comment_via_servo") as mock_post, \
         patch("backend.services.social_outreach.reddit_outreach.audit.log_outreach_event"):
        
        # Draft succeeds with would_post=True for all
        mock_draft.return_value = {
            "would_post": True,
            "draft": "test draft",
            "audit_id": 1,
        }
        
        # Post always fails
        mock_post.return_value = (False, "servo_timeout")
        
        loop = reddit_outreach.RedditOutreachLoop()
        report = loop.run_one_pass("test")
        
        # Should abort after SERVO_FAILURE_ABORT_THRESHOLD failures
        assert report["aborted"] == SERVO_FAILURE_ABORT_THRESHOLD
        assert report["reason"] == "servo_threshold_hit"
        # Should not have tried all 5 threads
        assert mock_post.call_count == SERVO_FAILURE_ABORT_THRESHOLD
