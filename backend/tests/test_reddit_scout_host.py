"""Reddit scout only fetches from an exact Reddit host, never a look-alike."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.api.social_outreach_api import _reddit_thread_path  # noqa: E402


@pytest.mark.parametrize("url,path", [
    ("https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/", "/r/LocalLLaMA/comments/abc123/title/"),
    ("https://old.reddit.com/r/x/comments/1/", "/r/x/comments/1/"),
    ("http://reddit.com/r/x/comments/1?utm=1#top", "/r/x/comments/1"),
])
def test_accepts_reddit_hosts(url, path):
    assert _reddit_thread_path(url) == path


@pytest.mark.parametrize("url", [
    "https://reddit.com.evil.example/r/x/comments/1/",
    "https://evil.example/reddit.com/r/x/",
    "https://169.254.169.254/latest/meta-data?reddit.com",
    "ftp://www.reddit.com/r/x/",
    "https://redditx.com/r/x/",
    "not a url",
])
def test_rejects_lookalikes_and_other_hosts(url):
    assert _reddit_thread_path(url) is None
