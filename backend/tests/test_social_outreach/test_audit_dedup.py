"""recent_thread_ids must dedupe within window and ignore stale."""
from datetime import datetime, timedelta, timezone
from backend.services.social_outreach import audit
from backend.models import db, SocialOutreachLog


def test_recent_thread_ids_excludes_old_entries(app):
    """A post 200 hours old should NOT show up in a 168-hour window."""
    with app.app_context():
        old = SocialOutreachLog(
            platform="reddit", target_thread_id="old1",
            action="post", status="posted",
            created_at=datetime.now(timezone.utc) - timedelta(hours=200),
        )
        recent = SocialOutreachLog(
            platform="reddit", target_thread_id="recent1",
            action="post", status="posted",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.session.add_all([old, recent])
        db.session.commit()
        
        ids = audit.recent_thread_ids("reddit", hours=168)
        assert "recent1" in ids
        assert "old1" not in ids


def test_recent_thread_ids_only_includes_posted_status(app):
    """Only status='posted' rows should be included in dedup."""
    with app.app_context():
        posted = SocialOutreachLog(
            platform="reddit", target_thread_id="posted1",
            action="post", status="posted",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        drafted = SocialOutreachLog(
            platform="reddit", target_thread_id="drafted1",
            action="post", status="drafted",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.session.add_all([posted, drafted])
        db.session.commit()
        
        ids = audit.recent_thread_ids("reddit", hours=168)
        assert "posted1" in ids
        assert "drafted1" not in ids
