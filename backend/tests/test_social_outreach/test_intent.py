"""Natural-language outreach intent: classify then dispatch (no placebo queue)."""
from unittest.mock import MagicMock, patch

import pytest


def _classifier(payload: dict):
    """Return a classifier fn that ignores prompts and returns payload."""

    def _fn(system, user):
        return dict(payload)

    return _fn


def test_status_question_never_queues(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "status",
        "platform": "youtube",
        "topics": [],
        "draft_id": None,
        "confidence": 0.95,
        "reason": "user asked for status",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.intent.kill_switch.cadence_status", return_value={}), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:

        result = execute_outreach_intent(
            "what is the status of the youtube outreach item?",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is True
        assert result["intent"] == "status"
        assert result.get("task_ids") == []
        assert result.get("refused") is False
        mock_q.assert_not_called()


def test_nonsense_refuses_never_queues(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "refuse",
        "platform": None,
        "topics": [],
        "draft_id": None,
        "confidence": 0.99,
        "reason": "off-topic nursery rhyme",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:
        result = execute_outreach_intent(
            "did mary have a little lamb?",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is False
        assert result["refused"] is True
        assert result.get("task_ids") == []
        mock_q.assert_not_called()


def test_scout_youtube_offline_ai_comfyui_queues(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "scout_and_draft",
        "platform": "youtube",
        "topics": ["Offline AI", "ComfyUI"],
        "draft_id": None,
        "confidence": 0.92,
        "reason": "user asked to comment on youtube about those topics",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:

        mock_q.return_value = {
            "task_id": 42,
            "job_id": "task_42",
            "message": "queued",
        }
        result = execute_outreach_intent(
            "comment on some youtube videos regarding Offline AI or ComfyUI",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is True
        assert 42 in result["task_ids"]
        mock_q.assert_called_once()
        kwargs = mock_q.call_args
        assert kwargs[0][0] == "youtube"
        assert "Offline AI" in kwargs[1]["keyword_profiles"]
        assert "ComfyUI" in kwargs[1]["keyword_profiles"]
        assert kwargs[1]["chain_draft"] is True


def test_youtube_scout_is_not_queued_while_web_access_is_off(app):
    """The YouTube scout searches the web, so with web access off (the default,
    no setting row here) nothing is queued and the reply says how to turn it on.
    The real queue_outreach_run runs; it refuses before any Task or Celery call."""
    from backend.models import Task
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "scout_and_draft",
        "platform": "youtube",
        "topics": ["Offline AI", "ComfyUI"],
        "draft_id": None,
        "confidence": 0.92,
        "reason": "user asked to comment on youtube about those topics",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.kill_switch.is_enabled", return_value=True):
        result = execute_outreach_intent(
            "comment on some youtube videos regarding Offline AI or ComfyUI",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is False
        assert result["message"] == "Web access is disabled. Enable it in Settings to scout YouTube for outreach."
        assert result["task_ids"] == []
        assert Task.query.count() == 0


def test_low_confidence_scout_refuses(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "scout_and_draft",
        "platform": "youtube",
        "topics": ["maybe AI?"],
        "draft_id": None,
        "confidence": 0.4,
        "reason": "uncertain",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:
        result = execute_outreach_intent(
            "maybe do something with youtube?",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is False
        assert result["refused"] is True
        mock_q.assert_not_called()


def test_structured_platform_topics_skips_classifier(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:

        mock_q.return_value = {"task_id": 7, "job_id": "task_7"}
        # classifier would refuse if called — ensure we never call it
        bad_clf = MagicMock(side_effect=AssertionError("classifier should not run"))
        result = execute_outreach_intent(
            "",
            platform="youtube",
            topics=["Offline AI", "ComfyUI"],
            action="comment",
            created_by="test",
            classifier=bad_clf,
        )
        assert result["ok"] is True
        mock_q.assert_called_once()
        bad_clf.assert_not_called()


def test_execute_intent_kill_switch_off(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "scout_and_draft",
        "platform": "youtube",
        "topics": ["ollama"],
        "draft_id": None,
        "confidence": 0.9,
        "reason": "scout",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=False):
        result = execute_outreach_intent(
            "scout youtube about ollama",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is False
        assert "disabled" in (result.get("error") or "").lower()


def test_intent_api_endpoint(app, client):
    from backend.api import social_outreach_api

    app.register_blueprint(social_outreach_api.social_outreach_bp)

    with patch(
        "backend.services.social_outreach.intent.execute_outreach_intent",
        return_value={
            "ok": True,
            "intent": "scout_and_draft",
            "message": "queued",
            "task_ids": [1],
            "plan": {"platform": "youtube"},
            "refused": False,
        },
    ):
        resp = client.post(
            "/api/social-outreach/intent",
            json={"text": "comment on youtube regarding Offline AI"},
        )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_intent_api_refuse_is_http_200(app, client):
    from backend.api import social_outreach_api

    app.register_blueprint(social_outreach_api.social_outreach_bp)

    with patch(
        "backend.services.social_outreach.intent.execute_outreach_intent",
        return_value={
            "ok": False,
            "intent": "refuse",
            "refused": True,
            "message": "Outreach: off-topic",
            "error": "off-topic",
            "task_ids": [],
            "queued": [],
        },
    ):
        resp = client.post(
            "/api/social-outreach/intent",
            json={"text": "did mary have a little lamb?"},
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["refused"] is True
    assert body["ok"] is False


def test_scout_reddit_searches_for_the_named_topics(app):
    """A Reddit scout with topics is one recon run that searches for them and
    drafts what it finds."""
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "scout_and_draft",
        "platform": "reddit",
        "topics": ["ComfyUI", "Stable Diffusion"],
        "draft_id": None,
        "confidence": 0.91,
        "reason": "user asked to market on reddit about those topics",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:

        mock_q.return_value = {"task_id": 99, "job_id": "task_99", "message": "queued"}
        result = execute_outreach_intent(
            "market Guaardvark on reddit about ComfyUI",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is True
        assert result.get("posts_require_approve") is True
        assert mock_q.call_count == 1
        recon_call = mock_q.call_args_list[0]
        assert recon_call[0][0] == "recon"
        assert recon_call[1]["search_queries"] == ["ComfyUI", "Stable Diffusion"]
        assert recon_call[1]["chain_draft"] is True
        assert result["follow"]["task_ids"] == [99]
        assert result["follow"]["platform"] == "reddit"


def test_a_scout_with_no_topic_searches_the_profile_keywords(app):
    """"reply to some posts on Reddit" names no topic: the search phrases come
    from the outreach profile, and the answer says so."""
    from backend.services.social_outreach import intent

    clf = _classifier({
        "intent": "scout_and_draft", "platform": "reddit", "topics": [],
        "draft_id": None, "confidence": 0.95, "reason": "engage on reddit",
    })
    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=True), \
         patch.object(intent, "_profile_queries", return_value=["voice cloning", "comfyui"]), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:
        mock_q.return_value = {"task_id": 7, "job_id": "task_7"}
        result = intent.execute_outreach_intent(
            "reply to some posts on Reddit", created_by="test", classifier=clf,
        )
    assert result["ok"] is True
    assert mock_q.call_args[1]["search_queries"] == ["voice cloning", "comfyui"]
    assert result["plan"]["topics_from_profile"] is True
    assert "from your outreach profile" in result["message"]


def test_a_scout_with_no_topic_and_no_profile_keywords_is_refused(app):
    from backend.services.social_outreach import intent

    clf = _classifier({
        "intent": "scout_and_draft", "platform": "reddit", "topics": [],
        "draft_id": None, "confidence": 0.95, "reason": "engage on reddit",
    })
    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=True), \
         patch.object(intent, "_profile_queries", return_value=[]), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:
        result = intent.execute_outreach_intent(
            "reply to some posts on Reddit", created_by="test", classifier=clf,
        )
    assert result["refused"] is True
    mock_q.assert_not_called()


def test_a_facebook_scout_is_refused_with_what_works(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "scout_and_draft", "platform": "facebook", "topics": [],
        "draft_id": None, "confidence": 0.95, "reason": "engage on facebook",
    })
    with app.app_context(), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:
        result = execute_outreach_intent(
            "find some Facebook posts to reply to", created_by="test", classifier=clf,
        )
    assert result["refused"] is True
    assert "Paste a post's link" in result["message"]
    mock_q.assert_not_called()


@pytest.mark.parametrize("text, intent_name, platform", [
    ("https://www.reddit.com/r/LocalLLaMA/comments/abc123/a_thread/", "draft_url", "reddit"),
    ("reply to this https://youtu.be/dQw4w9WgXcQ please", "draft_url", "youtube"),
    ("post draft 42", "post", None),
    ("publish #7 now", "post", None),
])
def test_a_link_and_a_numbered_post_are_read_without_the_model(text, intent_name, platform):
    from backend.services.social_outreach.intent import _typed_request

    typed = _typed_request(text)
    assert typed["intent"] == intent_name
    assert typed["platform"] == platform


@pytest.mark.parametrize("text", ["post something on reddit", "what's the status?", "approve draft 4"])
def test_other_requests_go_to_the_classifier(text):
    from backend.services.social_outreach.intent import _typed_request

    assert _typed_request(text) is None


@pytest.mark.parametrize("key", ["social_outreach_supervised", "outreach_require_independent_check"])
def test_queued_ok_always_says_posts_wait_for_approval(app, key):
    """A setting left by an older install cannot make a scout run claim that
    drafts may post on their own."""
    from backend.models import Setting, db
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "scout_and_draft",
        "platform": "youtube",
        "topics": ["Ollama"],
        "draft_id": None,
        "confidence": 0.9,
        "reason": "scout",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:
        db.session.add(Setting(key=key, value="false"))
        db.session.commit()
        mock_q.return_value = {"task_id": 3, "job_id": "task_3"}
        result = execute_outreach_intent(
            "comment on youtube about Ollama",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is True
        assert result.get("posts_require_approve") is True
        assert result.get("supervised") is True
        assert "nothing posts until you approve it" in (result.get("message") or "")


def test_approving_an_x_draft_by_intent_is_refused_with_the_reason(app):
    from backend.models import SocialOutreachLog, db
    from backend.services.social_outreach import platforms
    from backend.services.social_outreach.intent import execute_outreach_intent

    with app.app_context():
        row = SocialOutreachLog(platform="twitter", action="comment", status="drafted", draft_text="hi")
        db.session.add(row)
        db.session.commit()
        result = execute_outreach_intent(
            f"approve draft {row.id}",
            created_by="test",
            classifier=_approve_classifier(row.id),
        )
        assert result["ok"] is False
        assert result["error"] == platforms.refusal_for("twitter")
        db.session.expire_all()
        assert db.session.get(SocialOutreachLog, row.id).status == "unsupported"


# ---- approve and reject act only on a draft id the user typed -----------------------

def _approve_classifier(draft_id):
    return _classifier({
        "intent": "approve",
        "platform": None,
        "topics": [],
        "draft_id": draft_id,
        "confidence": 0.9,
        "reason": "approve a draft",
    })


@pytest.mark.parametrize("text, model_id, expected", [
    ("approve the newest draft", 17, None),
    ("approve #42", None, 42),
    ("approve #42", 17, 42),
    ("approve draft 42", 41, 42),
    ("approve draft 42", 42, 42),
    ("approve id 42", None, 42),
    ("approve 42", 42, 42),
    ("approve the one from 2.5 hours ago", 5, None),
    # An explicit "draft N" / "#N" beats the model's id and any other number.
    ("approve draft 42 from 3 days ago", 3, 42),
    ("approve draft 42 from 3 days ago", None, 42),
    ("approve #7", 17, 7),
    ("approve #7", None, 7),
    ("approve #7, the one from 3 days ago", 3, 7),
])
def test_the_draft_id_is_one_the_user_typed(text, model_id, expected):
    from backend.services.social_outreach.intent import classify_outreach_utterance

    result = classify_outreach_utterance(text, classifier=_approve_classifier(model_id))

    assert result["draft_id"] == expected


def test_an_inferred_draft_id_is_refused_not_approved(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.transitions.approve") as approve:
        result = execute_outreach_intent(
            "approve the newest draft",
            created_by="test",
            classifier=_approve_classifier(17),
        )

    approve.assert_not_called()
    assert result["refused"] is True
    assert "Approve needs a draft id" in result["message"]
