"""A GET or HEAD request changes nothing.

Any web page can make a browser send a GET (an <img>, a link) or a HEAD (a
fetch in no-cors mode) to the backend without asking first, and
backend/utils/cross_site_guard.py lets both through by design; for the
Guaardvark machine without an API key, auth_guard trusts them too. The routes
here used to act on one of them. Each now acts only on POST or DELETE, or
leaves nothing behind.

Blueprints are registered on a bare Flask app; the database, chat manager,
orchestrator, swarm service and video batch generator are stand-ins, and
files go to a temporary directory. No backend, GPU, database or network.
"""

import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

import backend.config as backend_config
import backend.models as models
from backend.api import batch_video_generation_api, enhanced_chat_api, memory_api, swarm_api
from backend.api.diagnostics_api import diagnostics_bp
from backend.api.interconnector_api import interconnector_bp
from backend.api.simple_chat_api import simple_chat_bp
from backend.services.system_mapper import tool_graph

POST_ONLY = (
    "/api/meta/test-llm",
    "/api/meta/diagnostics/export",
    "/api/meta/quality-scorecard",
    "/api/simple-chat/health",
    "/api/interconnector/nodes/<node_id>/heartbeat",
)


def _client(*blueprints):
    app = Flask(__name__)
    for blueprint in blueprints:
        app.register_blueprint(blueprint)
    return app.test_client()


def _fail(what):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"{what} ran on a read")
    return fail


def test_routes_that_run_the_model_or_write_take_post_only():
    client = _client(diagnostics_bp, simple_chat_bp, interconnector_bp)
    methods = {rule.rule: rule.methods for rule in client.application.url_map.iter_rules()}
    for path in POST_ONLY:
        assert "POST" in methods[path], path
        assert "GET" not in methods[path] and "HEAD" not in methods[path], path


# ---- chat history ------------------------------------------------------------

class _Rows:
    """A query that counts and lists nothing and records a delete."""

    def __init__(self, log):
        self.log = log

    def filter(self, *_args):
        return self

    order_by = limit = filter

    def count(self):
        return 0

    def all(self):
        return []

    def delete(self):
        self.log.append("delete")
        return 0


@pytest.fixture
def chat_db(monkeypatch, tmp_path):
    log = []
    session = SimpleNamespace(
        query=lambda _model: _Rows(log),
        get=lambda *_args: None,
        add=_fail("adding a row"),
        commit=lambda: log.append("commit"),
    )
    monkeypatch.setattr(models, "db", SimpleNamespace(session=session))
    monkeypatch.setattr(backend_config, "CONTEXT_PERSISTENCE_DIR", str(tmp_path / "context"))
    monkeypatch.setattr(backend_config, "STORAGE_DIR", str(tmp_path / "storage"))
    return log


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_reading_chat_history_counts_deletes_nothing(chat_db, monkeypatch, method):
    monkeypatch.setattr(enhanced_chat_api, "get_chat_manager", _fail("clearing the chat manager"))
    client = _client(enhanced_chat_api.enhanced_chat_bp)
    response = client.open("/api/enhanced-chat/history/all", method=method)
    assert response.status_code == 200
    assert chat_db == []


def test_delete_still_clears_chat_history(chat_db, monkeypatch):
    manager = SimpleNamespace(chat_engines={})
    monkeypatch.setattr(enhanced_chat_api, "get_chat_manager", lambda: manager)
    client = _client(enhanced_chat_api.enhanced_chat_bp)
    assert client.delete("/api/enhanced-chat/history/all").status_code == 200
    assert chat_db == ["delete", "delete", "commit"]


def test_reading_a_sessions_history_creates_no_session(chat_db, monkeypatch):
    orchestrator = SimpleNamespace(_session_to_plan_id={}, _active_plans={})
    monkeypatch.setitem(sys.modules, "backend.services.orchestrator_service",
                        SimpleNamespace(get_orchestrator=lambda: orchestrator))
    client = _client(enhanced_chat_api.enhanced_chat_bp)
    response = client.get("/api/enhanced-chat/new-session-id/history")
    assert response.status_code == 200
    assert response.get_json()["messages"] == []
    assert chat_db == []


# ---- memory, swarm -----------------------------------------------------------

@pytest.mark.parametrize("method", ["GET", "POST"])
def test_recall_debug_does_not_count_as_recall(monkeypatch, method):
    calls = []
    monkeypatch.setattr(memory_api, "_query_memories", lambda **kwargs: calls.append(kwargs) or [])
    client = _client(memory_api.memory_bp)
    kwargs = {"json": {"query": "x"}} if method == "POST" else {"query_string": {"query": "x"}}
    assert client.open("/api/memory/recall-debug", method=method, **kwargs).status_code == 200
    assert calls and calls[0]["count_access"] is False


def test_head_on_swarm_bus_state_writes_nothing(monkeypatch):
    monkeypatch.setattr(swarm_api, "_proxy_post", _fail("writing bus state"))
    monkeypatch.setattr(swarm_api, "_proxy_get", lambda _path: ({"state": {}}, 200))
    client = _client(swarm_api.swarm_bp)
    assert client.head("/api/swarm/s1/bus/state").status_code == 200


# ---- files left behind -------------------------------------------------------

def test_a_video_batch_download_leaves_no_temp_file(monkeypatch, tmp_path):
    batches = tmp_path / "batches"
    (batches / "VideoBatch_1").mkdir(parents=True)
    (batches / "VideoBatch_1" / "clip.mp4").write_bytes(b"not really a video")
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(temp))
    monkeypatch.setattr(batch_video_generation_api, "get_batch_video_generator",
                        lambda: SimpleNamespace(base_output_dir=batches))
    client = _client(batch_video_generation_api.batch_video_bp)

    response = client.get("/api/batch-video/download/VideoBatch_1")
    assert response.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(response.data)).namelist() == ["clip.mp4"]
    response.close()
    assert list(temp.iterdir()) == []


# ---- the system map ----------------------------------------------------------

def _guaardvark_shaped(root: Path) -> Path:
    for rel, text in {
        "backend/tools/tool_registry_init.py": "def register():\n    register_tool(EchoTool())\n",
        "backend/services/unified_chat_engine.py": "CORE_TOOLS = ['echo']\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def test_an_uploaded_repository_is_mapped_without_running_its_code(monkeypatch, tmp_path):
    import subprocess

    from backend.services.system_mapper import core

    monkeypatch.setattr(backend_config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(core, "_git_listed_files", lambda start: None)
    monkeypatch.setattr(subprocess, "run", _fail("starting a process"))
    monkeypatch.setattr(subprocess, "Popen", _fail("starting a process"))
    result = tool_graph.analyze(_guaardvark_shaped(tmp_path / "uploads" / "repo"))
    assert result["stats"]["tool_registry_source"] == "static"


def test_a_local_checkout_is_read_the_same_way(monkeypatch, tmp_path):
    import subprocess

    from backend.services.system_mapper import core

    monkeypatch.setattr(backend_config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(core, "_git_listed_files", lambda start: None)
    monkeypatch.setattr(subprocess, "run", _fail("starting a process"))
    monkeypatch.setattr(subprocess, "Popen", _fail("starting a process"))
    result = tool_graph.analyze(_guaardvark_shaped(tmp_path / "checkout"))
    assert result["stats"]["tool_registry_source"] == "static"

