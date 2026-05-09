"""Cadence daily-cap must reject posts after the limit."""
import pytest
from unittest.mock import patch

from backend.services.social_outreach import kill_switch


def test_cadence_blocks_when_daily_cap_hit(monkeypatch):
    """After 8 posts in 24h, cadence_allows_post must return False."""
    fake_count = {"reddit": 8}
    
    def fake_count_recent(platform):
        return fake_count.get(platform, 0)
    
    # Need to patch the actual Redis access inside cadence_allows_post
    # Let me mock _get_redis to return a fake Redis client
    class FakeRedis:
        def __init__(self):
            self.data = {}
        
        def get(self, key):
            return self.data.get(key)
        
        def zremrangebyscore(self, key, min_score, max_score):
            pass
        
        def zcard(self, key):
            if "reddit" in key:
                return 8
            return 0
    
    fake_redis = FakeRedis()
    monkeypatch.setattr("backend.services.social_outreach.kill_switch._get_redis", lambda: fake_redis)
    
    allowed, reason = kill_switch.cadence_allows_post("reddit")
    assert allowed is False
    assert "cap" in (reason or "").lower() or "limit" in (reason or "").lower()


def test_cadence_allows_when_under_cap(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.data = {}
        
        def get(self, key):
            return self.data.get(key)
        
        def zremrangebyscore(self, key, min_score, max_score):
            pass
        
        def zcard(self, key):
            return 5
    
    fake_redis = FakeRedis()
    monkeypatch.setattr("backend.services.social_outreach.kill_switch._get_redis", lambda: fake_redis)
    
    allowed, _ = kill_switch.cadence_allows_post("reddit")
    assert allowed is True
