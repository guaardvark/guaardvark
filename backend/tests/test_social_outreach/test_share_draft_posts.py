"""A share draft made with outreach_draft_post can actually be posted.

The watched poster reads the subreddit from the row's target_url, so the tool
has to queue a share row that points at the subreddit named in share_target.
The persona is a stand-in (no LLM), the Reddit submit poster is a recorder
(no browser), and the tool's HTTP call is answered by the test client.
"""

from __future__ import annotations

import json
import time

import pytest

from backend.api import social_outreach_api
from backend.models import SocialOutreachLog, db
from backend.services.social_outreach import audit, kill_switch
from backend.tasks import social_outreach_tasks as tasks
from backend.tools import outreach_tools
from backend.utils.backend_http import BackendError, BackendResponse


@pytest.fixture(autouse=True)
def _posting_needs_web_access(web_access_on):
    """These tests post; posting is gated on web access being on."""



@pytest.fixture
def draft_post(app, client, monkeypatch, tmp_path):
    """outreach_draft_post on the MCP path, wired to the test app's routes."""
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    monkeypatch.setattr(audit, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    seen = {"persona_calls": 0, "title": "Local-first AI studio"}

    def backend(method, path, payload=None, **kwargs):
        resp = client.open(path, method=method, json=payload)
        body = resp.get_json()
        if resp.status_code >= 400:
            raise BackendError("http", body.get("error"), status=resp.status_code, body=body)
        return BackendResponse(status=resp.status_code, body=body, data=body)

    def persona(**kwargs):
        seen["persona_calls"] += 1
        seen["context"] = kwargs["context"]
        text = json.dumps({"title": seen["title"], "body": "b", "link_url": kwargs["context"]["link_url"]})
        return {"draft": text, "grade": 0.9, "reason": "r"}

    monkeypatch.setattr(outreach_tools, "request_json", backend)
    monkeypatch.setattr(outreach_tools.persona, "draft_outreach_text", persona)

    def call(**arguments):
        tool = outreach_tools.OutreachDraftPostTool()
        tool.set_context({"transport": "mcp"})
        return tool.execute(**arguments)

    call.seen = seen
    call.client = client
    return call


def _rows():
    db.session.expire_all()
    return SocialOutreachLog.query.order_by(SocialOutreachLog.id).all()


def test_share_draft_is_queued_at_its_subreddit(draft_post):
    result = draft_post(platform="reddit", mode="share", share_target="r/SideProject")

    assert result.success, result.error
    assert result.output["target_url"] == "https://www.reddit.com/r/SideProject"
    row = _rows()[0]
    assert (row.action, row.status) == ("share", "drafted")
    assert row.target_url == "https://www.reddit.com/r/SideProject"
    assert draft_post.seen["context"]["target"] == "r/SideProject"


@pytest.mark.parametrize("share_target", [
    "SideProject", "/r/SideProject", "r/SideProject/", "https://www.reddit.com/r/SideProject/",
    "https://old.reddit.com/r/SideProject/comments/abc/x/",
])
def test_share_target_spellings_name_the_same_subreddit(draft_post, share_target):
    result = draft_post(platform="reddit", mode="share", share_target=share_target)

    assert result.output["target_url"] == "https://www.reddit.com/r/SideProject"


def test_share_ignores_a_stray_target_url(draft_post):
    draft_post(platform="reddit", mode="share", share_target="r/selfhosted",
               target_url="https://www.reddit.com/r/Other/comments/abc/x/")

    assert _rows()[0].target_url == "https://www.reddit.com/r/selfhosted"


@pytest.mark.parametrize("arguments, needle", [
    ({"platform": "reddit", "share_target": "my followers"}, "not a subreddit"),
    ({"platform": "reddit", "share_target": "u/someone"}, "not a subreddit"),
    ({"platform": "reddit", "share_target": "https://example.com/r/x"}, "not a subreddit"),
    ({"platform": "reddit"}, "requires share_target"),
    ({"platform": "twitter", "share_target": "my timeline"}, "does not post to X"),
    ({"platform": "discord", "share_target": "#general"}, "can only be posted on reddit"),
])
def test_share_that_could_never_post_is_refused_before_drafting(draft_post, arguments, needle):
    result = draft_post(mode="share", **arguments)

    assert not result.success
    assert needle in result.error
    assert draft_post.seen["persona_calls"] == 0
    assert _rows() == []


def test_share_draft_without_a_title_is_not_queued(draft_post):
    draft_post.seen["title"] = ""

    result = draft_post(platform="reddit", mode="share", share_target="r/SideProject")

    assert not result.success
    assert "no title" in result.error
    assert _rows() == []


def test_an_approved_share_draft_is_submitted_to_its_subreddit(
    outreach_profile, draft_post, app, monkeypatch, fake_redis, watching,
):
    submitted = []

    def submit(subreddit, title, link_url, *, before_submit=None):
        assert before_submit is not None and before_submit()
        submitted.append((subreddit, title, link_url))
        return True, "ok"

    def with_ctx(fn, *args, **kwargs):
        with app.app_context():
            return fn(*args, **kwargs)

    def record_post(url, json=None, headers=None, timeout=None):
        return draft_post.client.post("/api/social-outreach/record-post", json=json, headers=headers)

    monkeypatch.setattr(tasks, "_with_app_context", with_ctx)
    monkeypatch.setattr(kill_switch, "is_enabled", lambda: True)
    monkeypatch.setattr(kill_switch, "cadence_allows_post", lambda platform: (True, None))
    monkeypatch.setattr(kill_switch, "record_post", lambda platform: None)
    monkeypatch.setattr("backend.services.social_outreach.self_share._submit_post_via_servo", submit)
    monkeypatch.setattr("requests.post", record_post)

    row_id = draft_post(platform="reddit", mode="share", share_target="r/SideProject").output["audit_id"]
    assert draft_post.client.post(f"/api/social-outreach/approve/{row_id}").status_code == 200

    result = tasks.post_watched_draft.run(row_id, time.time())

    assert submitted == [("SideProject", "Local-first AI studio", "https://example.com")]
    assert result["posted"] is True
    assert _rows()[0].status == "posted"


# ---------------------------------------------------------------------------
# After the submit click, the post counts only when the page shows it landed
# ---------------------------------------------------------------------------

def _filled_form(subreddit, title, link="https://guaardvark.com", **changes):
    form = {
        "url": f"https://www.reddit.com/r/{subreddit}/submit/?type=LINK",
        "kind": "LINK", "title": title, "link": link,
        "button": {"disabled": False, "shown": True, "x": 600, "y": 900},
    }
    form.update(changes)
    return form


def _submit_with_page(monkeypatch, page, subreddit="x", title="Local-first AI studio"):
    """Run the real Reddit submit poster on a form that holds the draft, with
    the post-submit page read answering ``page``."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from backend.services.social_outreach import self_share

    class Service:
        is_active = False

        def execute_task(self, task, screen):
            return SimpleNamespace(success=True, reason="ok")

    seen = []

    def evaluate(expression):
        seen.append(expression)
        return page

    monkeypatch.setattr("backend.services.agent_control_service.get_agent_control_service",
                        lambda: Service())
    monkeypatch.setattr("backend.utils.agent_display_utils.start_agent_display_if_needed",
                        lambda: True)
    monkeypatch.setattr("backend.services.local_screen_backend.LocalScreenBackend", MagicMock)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr(self_share, "bidi_evaluate_json", evaluate)
    monkeypatch.setattr(self_share, "bidi_reachable", lambda *a, **k: (True, ""))
    monkeypatch.setattr(self_share, "_bidi_navigate", lambda *a, **k: True)
    monkeypatch.setattr(self_share, "_read_form", lambda: (_filled_form(subreddit, title), ""))
    monkeypatch.setattr(self_share, "_LANDED_WAIT_S", 0)
    result = self_share._submit_post_via_servo(subreddit, title, "https://guaardvark.com")
    return result, seen


def test_share_still_on_the_submit_page_is_unverified(monkeypatch):
    url = "https://www.reddit.com/r/x/submit"
    (ok, reason), _ = _submit_with_page(monkeypatch, ({"url": url, "title_on_page": False}, ""))
    assert ok is False
    assert reason == f"submit_unverified: {url}"


def test_share_on_the_new_post_with_its_title_is_posted(monkeypatch):
    page = {"url": "https://www.reddit.com/r/x/comments/abc123/", "title_on_page": True}
    (ok, reason), seen = _submit_with_page(monkeypatch, (page, ""))
    assert (ok, reason) == (True, "ok")
    assert '"Local-first AI studio"' in seen[0]


def test_share_post_page_without_the_title_is_unverified(monkeypatch):
    page = {"url": "https://www.reddit.com/r/x/comments/abc123/other_post/", "title_on_page": False}
    (ok, reason), _ = _submit_with_page(monkeypatch, (page, ""))
    assert ok is False
    assert reason.startswith("submit_unverified: https://www.reddit.com/r/x/comments/abc123/")


def test_share_post_in_another_subreddit_is_unverified(monkeypatch):
    page = {"url": "https://www.reddit.com/r/other/comments/abc123/", "title_on_page": True}
    (ok, reason), _ = _submit_with_page(monkeypatch, (page, ""))
    assert ok is False


def test_share_listed_on_the_feed_after_the_submit_is_posted(monkeypatch):
    # Reddit can return to the subreddit's feed after Post; the new post is
    # listed there with its permalink.
    page = {"url": "https://www.reddit.com/r/x/", "title_on_page": True,
            "listed": "/r/x/comments/1x1ar00/local_first_ai_studio/"}
    (ok, reason), seen = _submit_with_page(monkeypatch, (page, ""))
    assert (ok, reason) == (True, "ok")
    assert '"https://guaardvark.com"' in seen[0]


def test_share_listed_in_another_subreddit_is_unverified(monkeypatch):
    page = {"url": "https://www.reddit.com/", "title_on_page": True,
            "listed": "/r/other/comments/1x1ar00/local_first_ai_studio/"}
    (ok, reason), _ = _submit_with_page(monkeypatch, (page, ""))
    assert ok is False


def test_share_unreadable_page_is_unverified(monkeypatch):
    (ok, reason), _ = _submit_with_page(monkeypatch, (None, "connect failed: refused"))
    assert ok is False
    assert reason.startswith("submit_unverified") and "connect failed" in reason


@pytest.mark.parametrize("url, landed", [
    ("https://www.reddit.com/r/SideProject/comments/1abc2d/local_first/", True),
    ("https://old.reddit.com/r/sideproject/comments/1abc2d/", True),
    ("https://www.reddit.com/r/SideProject/comments/1abc2d", True),
    ("https://www.reddit.com/r/SideProject/submit", False),
    ("https://www.reddit.com/r/SideProjects/comments/1abc2d/", False),
    ("https://reddit.com.example.net/r/SideProject/comments/1abc2d/", False),
])
def test_post_url_shape(url, landed):
    from backend.services.social_outreach.self_share import _is_post_url
    assert _is_post_url(url, "SideProject") is landed


def test_share_refuses_before_touching_reddit_when_the_page_cannot_be_read(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from backend.services.social_outreach import self_share

    tasks = []

    class Service:
        is_active = False

        def execute_task(self, task, screen):
            tasks.append(task)
            return SimpleNamespace(success=True, reason="ok")

    monkeypatch.setattr("backend.services.agent_control_service.get_agent_control_service",
                        lambda: Service())
    monkeypatch.setattr("backend.utils.agent_display_utils.start_agent_display_if_needed",
                        lambda: True)
    monkeypatch.setattr("backend.services.local_screen_backend.LocalScreenBackend", MagicMock)
    monkeypatch.setattr(self_share, "bidi_reachable", lambda *a, **k: (False, "connect failed: refused"))
    ok, reason = self_share._submit_post_via_servo("test", "a title", "https://guaardvark.com")
    assert ok is False
    assert reason.startswith("page_check_unavailable: nothing was posted")
    assert "connect failed: refused" in reason
    assert tasks == []



# ---------------------------------------------------------------------------
# The form is posted only when it holds exactly the approved title and link
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("changes, needle", [
    ({}, ""),
    ({"url": "https://www.reddit.com/login/?dest=x"}, "not r/Test's submit form"),
    ({"kind": "TEXT"}, "not a link post"),
    ({"title": "A different title"}, "not the approved title"),
    ({"title": None}, "not the approved title"),
    ({"link": "https://example.com"}, "not the approved link"),
    ({"button": {"disabled": True, "shown": True, "x": 1, "y": 1}}, "Post button"),
    ({"button": None}, "Post button"),
])
def test_form_problem(changes, needle):
    from backend.services.social_outreach.self_share import _form_problem

    form = {**_filled_form("test", "Local-first AI studio"), **changes}
    problem = _form_problem(form, "Test", "Local-first  AI studio", "https://guaardvark.com")
    assert (needle in problem) if needle else problem == ""


def test_a_form_that_never_holds_the_draft_is_not_clicked(monkeypatch):
    from unittest.mock import MagicMock

    from backend.services.social_outreach import self_share

    screen = MagicMock()
    monkeypatch.setattr("backend.services.agent_control_service.get_agent_control_service",
                        lambda: MagicMock(is_active=False))
    monkeypatch.setattr("backend.utils.agent_display_utils.start_agent_display_if_needed",
                        lambda: True)
    monkeypatch.setattr("backend.services.local_screen_backend.LocalScreenBackend", lambda: screen)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr(self_share, "bidi_reachable", lambda *a, **k: (True, ""))
    monkeypatch.setattr(self_share, "_bidi_navigate", lambda *a, **k: True)
    monkeypatch.setattr(self_share, "_read_form", lambda: (_filled_form("test", "Prefilled by someone else"), ""))
    monkeypatch.setattr(self_share, "_FORM_WAIT_S", 0)
    ok, reason = self_share._submit_post_via_servo("test", "Local-first AI studio", "https://guaardvark.com")
    assert ok is False
    assert reason.startswith("form_not_ready: nothing was posted; the title field holds")
    screen.click.assert_not_called()
