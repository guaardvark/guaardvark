"""Hostname matching helper and the platform-detection sites that use it."""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.utils.hosts import host_matches, url_host, url_host_matches  # noqa: E402


@pytest.mark.parametrize("host,domains,expected", [
    ("reddit.com", ("reddit.com",), True),
    ("old.reddit.com", ("reddit.com",), True),
    ("REDDIT.COM.", ("reddit.com",), True),
    ("reddit.com.example.net", ("reddit.com",), False),
    ("notreddit.com", ("reddit.com",), False),
    ("box.com", ("twitter.com", "x.com"), False),
    ("mobile.twitter.com", ("twitter.com", "x.com"), True),
    ("", ("reddit.com",), False),
    (None, ("reddit.com",), False),
])
def test_host_matches(host, domains, expected):
    assert host_matches(host, *domains) is expected


def test_url_helpers():
    assert url_host("https://WWW.Reddit.com/r/x/") == "www.reddit.com"
    assert url_host("not a url") == ""
    assert url_host_matches("https://youtu.be/abc", "youtube.com", "youtu.be")
    assert not url_host_matches("https://youtube.com.evil.tld/watch?v=1", "youtube.com")


def test_platform_detection_rejects_lookalikes():
    from backend.api.social_outreach_api import _suggest_platform_from_host
    from backend.services import dom_metadata_extractor as dme

    owner = next(c for c in vars(dme).values() if isinstance(c, type) and hasattr(c, "detect_platform"))
    detect_platform = owner.detect_platform

    assert _suggest_platform_from_host("old.reddit.com") == "reddit"
    assert _suggest_platform_from_host("reddit.com.evil.tld") is None
    assert _suggest_platform_from_host("box.com") is None
    assert _suggest_platform_from_host("x.com") == "twitter"
    assert detect_platform("https://discord.gg/abc") == "discord"
    assert detect_platform("https://www.facebook.com/groups/1") == "facebook"
    assert detect_platform("https://facebook.com.evil.tld/") is None


def test_youtube_normalisation():
    from backend.services.social_outreach.youtube_outreach import _normalize_youtube_url

    assert _normalize_youtube_url("https://youtu.be/dQw4w9WgXcQ?t=1") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert _normalize_youtube_url("https://m.youtube.com/watch?v=abc") == "https://m.youtube.com/watch?v=abc"
    assert _normalize_youtube_url("https://youtube.com.evil.tld/watch?v=abc") is None
    assert _normalize_youtube_url("https://example.com/youtu.be/abc") is None
    assert _normalize_youtube_url("https://youtu.be/") is None


def test_url_regex_range_is_literal():
    from backend.utils import enhanced_rag_chunking as m

    src = open(m.__file__).read()
    pattern = re.search(r"urls = re\.findall\(r'(.+?)', text\)", src).group(1)
    assert "[$-_" not in pattern
    assert re.findall(pattern, "see https://example.com/a_b-c?d=1&e=2#x now") == ["https://example.com/a_b-c?d=1&e=2#x"]
    assert re.findall(pattern, "https://x.y/ABC and http://h/p%20q") == ["https://x.y/ABC", "http://h/p%20q"]
