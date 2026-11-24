from flask import Flask
import pytest


def _app():
    pytest.importorskip("llama_index")
    from backend.api.swarm_api import swarm_bp

    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(swarm_bp)
    return app


def test_swarm_diff_proxy(monkeypatch):
    pytest.importorskip("llama_index")
    from backend.api import swarm_api

    seen = {}

    def fake_get(path, timeout=swarm_api.SWARM_TIMEOUT):
        seen["path"] = path
        return {"success": True, "diff": "diff --git a/x b/x"}, 200

    monkeypatch.setattr(swarm_api, "_proxy_get", fake_get)

    with _app().test_client() as client:
        response = client.get("/api/swarm/swarm-1/diff/task-1")

    assert response.status_code == 200
    assert seen["path"] == "/swarm/swarm-1/diff/task-1"


def test_swarm_bus_routes_proxy(monkeypatch):
    pytest.importorskip("llama_index")
    from backend.api import swarm_api

    seen = {}

    def fake_post(path, json_data=None, timeout=swarm_api.SWARM_TIMEOUT):
        seen["path"] = path
        seen["json"] = json_data
        return {"success": True}, 200

    monkeypatch.setattr(swarm_api, "_proxy_post", fake_post)

    with _app().test_client() as client:
        response = client.post(
            "/api/swarm/swarm-1/bus/broadcast",
            json={"sender": "agent", "event_type": "note", "data": {"ok": True}},
        )

    assert response.status_code == 200
    assert seen["path"] == "/swarm/swarm-1/bus/broadcast"
    assert seen["json"]["event_type"] == "note"


def test_self_code_swarm_launch_blocks_dirty_tree(tmp_path, monkeypatch):
    pytest.importorskip("llama_index")
    from backend.api import swarm_api

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.setattr(swarm_api, "default_repo_root", lambda: repo)

    class Result:
        stdout = " M backend/app.py\n"

    monkeypatch.setattr(swarm_api.subprocess, "run", lambda *args, **kwargs: Result())

    with _app().test_client() as client:
        response = client.post(
            "/api/swarm/launch",
            json={"self_code": True, "plan_path": "plan.md", "repo_path": str(repo)},
        )

    assert response.status_code == 409


_DRAFT = """Here is the plan:

```markdown
# Swarm Plan: add a slug helper

## Task: Write the helper
- files: slug.py
- depends_on: none
- backend: any

Create slugify(text) and commit it.
```
"""


def _draft(monkeypatch, tmp_path, reply, dry_run=None):
    from backend.api import swarm_api
    from backend.utils import llm_service

    monkeypatch.setattr(llm_service, "run_llm_chat_prompt", lambda prompt, **k: reply)
    monkeypatch.setattr(swarm_api, "default_repo_root", lambda: tmp_path)
    monkeypatch.setattr("backend.config.GUAARDVARK_ROOT", str(tmp_path), raising=False)
    posted = {}

    def fake_post(path, json_data=None, timeout=swarm_api.SWARM_TIMEOUT):
        posted.update(path=path, body=json_data)
        return dry_run or ({"error": "Swarm service not running"}, 503)

    monkeypatch.setattr(swarm_api, "_proxy_post", fake_post)
    with _app().test_client() as client:
        return client.post("/api/swarm/draft-plan", json={"goal": "add a slug helper"}), posted


def test_draft_plan_returns_the_plan_without_the_chat_around_it(monkeypatch, tmp_path):
    response, posted = _draft(monkeypatch, tmp_path, _DRAFT, dry_run=(
        {"dry_run": True, "task_count": 1, "tasks": [{"title": "Write the helper"}]}, 200))

    data = response.get_json()["data"]
    assert response.status_code == 200
    assert data["markdown"].startswith("# Swarm Plan: add a slug helper")
    assert "```" not in data["markdown"] and "Here is the plan" not in data["markdown"]
    assert data["task_count"] == 1 and data["tasks"] == ["Write the helper"]
    # Checked by a dry run only, and the draft file does not stay behind.
    assert posted["path"] == "/swarm/launch" and posted["body"]["dry_run"] is True
    assert not list(tmp_path.rglob("plan_*.md"))


def test_draft_plan_refuses_a_reply_that_is_not_a_plan(monkeypatch, tmp_path):
    notice = "**File Generated**: `generated_file.md`\n\nYour file has been generated and saved."
    response, _ = _draft(monkeypatch, tmp_path, notice)
    assert response.status_code == 502
    assert response.get_json()["error"]["code"] == "NO_PLAN_IN_REPLY"


def test_draft_plan_still_answers_when_the_swarm_service_is_down(monkeypatch, tmp_path):
    response, _ = _draft(monkeypatch, tmp_path, _DRAFT)
    data = response.get_json()["data"]
    assert response.status_code == 200 and data["task_count"] is None
    assert "## Task: Write the helper" in data["markdown"]
