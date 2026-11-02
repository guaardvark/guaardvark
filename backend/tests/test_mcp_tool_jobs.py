"""Photo edits and animations called over MCP run as tool jobs.

Over MCP, edit_image, inpaint_image, outpaint_image, remove_background and
generate_animation hand the call to the backend, which starts it as a tool job
(backend/services/tool_jobs.py) and answers at once with a job id that
get_generation_status reads. Chat and the Tools page still run them inline.
The renders are stand-ins: no GPU, network or database is touched.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from types import SimpleNamespace

import pytest
from flask import Flask, current_app, has_app_context

from backend import config
from backend.mcp import config as mcp_config
from backend.services import tool_jobs
from backend.services.agent_tools import ToolResult
from backend.utils import backend_http, media_inputs as mi
from backend.utils.backend_http import BackendError, BackendResponse, is_mcp_caller

IMG = "/api/outputs/generated_images/edit_1.png"
EDITED = "/api/outputs/generated_images/edit_done.png"
JOB = "tooljob_abcdef_0123456789ab"


@pytest.fixture(autouse=True)
def fresh_jobs(monkeypatch):
    monkeypatch.setattr(tool_jobs, "_jobs", OrderedDict())
    monkeypatch.setattr(tool_jobs, "_PROCESS_TOKEN", "abcdef")
    monkeypatch.delenv("GUAARDVARK_IMAGE_VRAM_WAIT_S", raising=False)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "install"
    uploads, outputs = root / "data" / "uploads", root / "data" / "outputs"
    (outputs / "generated_images").mkdir(parents=True)
    (outputs / "generated_images" / "edit_1.png").write_bytes(b"png")
    uploads.mkdir(parents=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"png")
    monkeypatch.setattr(mcp_config, "load_config", lambda: mcp_config.MCPConfig())
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", root)
    monkeypatch.setattr(mi, "resources_root", lambda: str(outputs.resolve()))
    return SimpleNamespace(outputs=outputs, outside=outside)


def _finished(job_id: str) -> dict:
    snapshot = tool_jobs.wait(job_id, 5)
    assert snapshot["status"] in ("done", "failed"), snapshot
    return snapshot


# ---- the job runner ------------------------------------------------------------------------
def test_a_job_runs_on_its_own_thread_and_keeps_its_result():
    gate = threading.Event()

    def run():
        gate.wait(5)
        return ToolResult(success=True, output="ok", metadata={"image_url": EDITED})

    started = tool_jobs.submit("edit_image", run)
    assert started["job_id"].startswith("tooljob_abcdef_")
    assert tool_jobs.get(started["job_id"])["status"] in ("queued", "running")
    gate.set()
    done = _finished(started["job_id"])
    assert done["status"] == "done" and done["result"]["metadata"]["image_url"] == EDITED
    assert done["elapsed_s"] >= 0 and done["tool"] == "edit_image"


def test_a_failed_or_raising_run_is_a_failed_job():
    failed = tool_jobs.submit("outpaint_image", lambda: ToolResult(success=False, error="no model"))
    assert _finished(failed["job_id"])["result"]["error"] == "no model"

    def boom():
        raise RuntimeError("kaboom")

    raised = _finished(tool_jobs.submit("edit_image", boom)["job_id"])
    assert raised["status"] == "failed" and "kaboom" in raised["result"]["error"]


def test_the_job_thread_keeps_the_mcp_mark_and_the_app_context():
    seen = {}

    def run():
        seen["mcp"] = is_mcp_caller(SimpleNamespace(_context={}))
        seen["app"] = has_app_context() and current_app.name
        seen["in_job"] = tool_jobs.in_job()
        return ToolResult(success=True)

    with Flask("jobs-test").app_context():
        job = tool_jobs.submit("edit_image", run)
    _finished(job["job_id"])
    assert seen == {"mcp": True, "app": "jobs-test", "in_job": True}
    assert not tool_jobs.in_job()


def test_unknown_ids_say_whether_the_backend_restarted():
    reason, text = tool_jobs.missing("tooljob_000000_0123456789ab")
    assert reason == "restarted" and "before the Guaardvark backend last restarted" in text
    reason, text = tool_jobs.missing(JOB)
    assert reason == "unknown" and "No tool job" in text
    assert tool_jobs.missing("tooljob_typo")[0] == "unknown"


def test_finished_jobs_expire_and_the_registry_is_bounded(monkeypatch):
    monkeypatch.setattr(tool_jobs, "MAX_JOBS", 3)
    ids = [_finished(tool_jobs.submit("remove_background", lambda: ToolResult(success=True))["job_id"])["job_id"]
           for _ in range(5)]
    assert tool_jobs.get(ids[0]) is None and tool_jobs.get(ids[-1]) is not None
    assert len(tool_jobs._jobs) == 3
    monkeypatch.setattr(tool_jobs, "FINISHED_TTL_S", 0)
    time.sleep(0.01)
    assert tool_jobs.get(ids[-1]) is None and not tool_jobs._jobs


def test_too_many_active_jobs_are_refused(monkeypatch):
    monkeypatch.setattr(tool_jobs, "MAX_ACTIVE_JOBS", 2)
    gate = threading.Event()
    held = [tool_jobs.submit("edit_image", lambda: gate.wait(5) and ToolResult(success=True)) for _ in range(2)]
    with pytest.raises(tool_jobs.ToolJobsBusy):
        tool_jobs.submit("edit_image", lambda: ToolResult(success=True))
    gate.set()
    for job in held:
        _finished(job["job_id"])
    _finished(tool_jobs.submit("edit_image", lambda: ToolResult(success=True))["job_id"])


def test_a_job_waits_for_a_busy_gpu_and_reads_as_queued_meanwhile(monkeypatch):
    from backend.services.job_operation_gate import GpuBusyError
    from backend.tools import image_tools

    monkeypatch.setattr(tool_jobs, "GPU_WAIT_NOTE_S", 0.2)
    gate, noted, policy = threading.Event(), threading.Event(), {}

    def run():
        policy.update(image_tools._chat_gpu_wait() or {})
        policy["on_wait"]("GPU is held by video_render:other")
        noted.set()
        gate.wait(5)
        return ToolResult(success=True)

    job = tool_jobs.submit("generate_animation", run)
    assert noted.wait(5)
    waiting = tool_jobs.get(job["job_id"])
    assert waiting["status"] == "queued" and "held by video_render:other" in waiting["note"]
    assert policy["wait_s"] == 600.0 and policy["should_stop"] is None
    time.sleep(0.3)
    assert tool_jobs.get(job["job_id"])["status"] == "running"
    gate.set()
    _finished(job["job_id"])
    # Outside a job nothing waits, and a job's refusal after the wait reads as a job's.
    assert image_tools._job_gpu_wait() is None and image_tools._chat_gpu_wait() is None
    refusal = image_tools._gpu_refusal(GpuBusyError("held"), policy)
    assert "10 minutes" in refusal and "this job did not run" in refusal


def test_the_wait_bound_is_half_the_mcp_timeout_up_to_a_minute(monkeypatch):
    from backend.mcp.tools_adapter import _call_timeout

    monkeypatch.setattr(mcp_config, "load_config", lambda: mcp_config.MCPConfig(timeout_seconds=30))
    assert tool_jobs.wait_seconds() == 15
    monkeypatch.setattr(mcp_config, "load_config", lambda: mcp_config.MCPConfig())
    assert tool_jobs.wait_seconds() == tool_jobs.MAX_WAIT_S == 60
    # Starting a job plus the longest wait stays inside the adapter's ceiling.
    cfg = mcp_config.MCPConfig()
    assert _call_timeout(cfg, {"wait_for_result": True}) > tool_jobs.wait_seconds() + backend_http.DEFAULT_READ_TIMEOUT
    assert _call_timeout(cfg, {}) > backend_http.DEFAULT_READ_TIMEOUT


# ---- the backend side: POST /api/tools/execute and GET /api/tools/jobs/<id> ---------------
def _backend(tools):
    from backend.api.tools_api import tools_bp

    app = Flask(__name__)
    app.register_blueprint(tools_bp)
    by_name = {t.name: t for t in tools}
    app.tool_registry = SimpleNamespace(
        get_tool=by_name.get, execute_tool=lambda name, **params: by_name[name].execute(**params))
    return app


def test_an_mcp_call_in_the_backend_returns_a_job_and_other_callers_run_inline(tree, monkeypatch):
    from backend.tools import image_tools as it

    gate = threading.Event()

    def fake_edit(self, src, extra, *, instruction, steps, model, seed=None):
        gate.wait(5)
        return ToolResult(success=True, output=f"Image edited.\nImage URL: {EDITED}", metadata={"image_url": EDITED})

    monkeypatch.setattr(it.EditImageTool, "_edit", fake_edit)
    # An editing pack is installed; without one the call is refused before any job starts.
    monkeypatch.setattr(it.EditImageTool, "_pick_edit_backend", staticmethod(lambda model: "qwen"))
    app = _backend([it.EditImageTool(), it.InpaintImageTool()])
    with app.test_client() as client:
        body = {"tool_name": "edit_image", "parameters": {"instruction": "night", "image": IMG},
                "caller_transport": "mcp"}
        result = client.post("/api/tools/execute", json=body).get_json()["result"]
        job_id = result["metadata"]["job_id"]
        assert result["success"] and job_id in result["output"] and "get_generation_status" in result["output"]
        assert client.get(f"/api/tools/jobs/{job_id}").get_json()["data"]["status"] in ("queued", "running")
        gate.set()
        snapshot = client.get(f"/api/tools/jobs/{job_id}?wait_s=5").get_json()["data"]
        assert snapshot["status"] == "done" and snapshot["result"]["metadata"]["image_url"] == EDITED

        gone = client.get("/api/tools/jobs/tooljob_000000_0123456789ab")
        assert gone.status_code == 404 and gone.get_json()["reason"] == "restarted"

        # A bad input is refused in the request itself; no job is started.
        body["parameters"]["image"] = str(tree.outside)
        refused = client.post("/api/tools/execute", json=body).get_json()["result"]
        assert not refused["success"] and "MCP resources serve" in refused["error"]
        assert len(tool_jobs._jobs) == 1

        # Without the MCP mark (the Tools page, chat) the edit runs inline.
        inline = client.post("/api/tools/execute", json={
            "tool_name": "inpaint_image", "parameters": {"instruction": "x", "image": IMG}}).get_json()["result"]
        assert inline["success"] and inline["metadata"] == {"image_url": EDITED}
        assert len(tool_jobs._jobs) == 1


def test_get_generation_status_reads_a_tool_job_in_the_backend(monkeypatch):
    from backend.tools.image_tools import GenerationStatusTool

    job = tool_jobs.submit("generate_animation", lambda: ToolResult(
        success=True, output="Animation generated.",
        metadata={"gif_url": "/api/outputs/generated_animations/a.gif", "image_url": "/api/outputs/generated_animations/a.gif",
                  "video_url": "/api/outputs/generated_animations/a.mp4"}))
    res = GenerationStatusTool().execute(batch_id=job["job_id"], wait_seconds=5)
    assert res.success and f"Animation job {job['job_id']}: done" in res.output
    assert res.output.count("File: ") == 2 and "a.mp4" in res.output and "Elapsed:" in res.output
    missing = GenerationStatusTool().execute(batch_id=JOB)
    assert not missing.success and "No tool job" in missing.error


# ---- the MCP server side: forward, and optionally wait --------------------------------------
def _snapshot(status: str) -> dict:
    result = {"success": True, "output": f"Image edited.\nImage URL: {EDITED}", "error": None,
              "metadata": {"image_url": EDITED}} if status == "done" else None
    return {"job_id": JOB, "tool": "edit_image", "status": status, "note": None, "elapsed_s": 170.0,
            "result": result}


def _fake_backend(monkeypatch, status="done"):
    calls = []

    def fake_request_json(method, path, *, payload=None, read_timeout=None, **kw):
        calls.append(SimpleNamespace(method=method, path=path, payload=payload, read_timeout=read_timeout))
        if method == "POST":
            return BackendResponse(200, {"success": True, "result": {
                "success": True, "output": f"Queued as job {JOB}.", "error": None,
                "metadata": {"job_id": JOB, "queued": True}}}, None)
        if JOB in path:
            snapshot = _snapshot(status)
            return BackendResponse(200, {"success": True, "data": snapshot}, snapshot)
        raise BackendError("http", "Tool job was started before the Guaardvark backend last restarted.",
                           status=404, body={"reason": "restarted"})

    monkeypatch.setattr(backend_http, "request_json", fake_request_json)
    return calls


def _mcp(cls):
    tool = cls()
    tool.set_context({"transport": "mcp"})
    return tool


def test_the_mcp_server_forwards_each_job_tool_and_answers_with_the_job(monkeypatch):
    from backend.tools import image_tools as it

    calls = _fake_backend(monkeypatch)
    res = _mcp(it.EditImageTool).execute(instruction="night", image=IMG)
    assert res.success and JOB in res.output and len(calls) == 1
    sent = calls[0].payload
    assert sent["tool_name"] == "edit_image" and sent[backend_http.CALLER_TRANSPORT_FIELD] == "mcp"
    assert "wait_for_result" not in sent["parameters"]
    for cls, args in ((it.InpaintImageTool, {"instruction": "x", "image": IMG}),
                      (it.OutpaintImageTool, {"image": IMG}),
                      (it.RemoveBackgroundTool, {"image": IMG}),
                      (it.AnimationGeneratorTool, {"prompt": "a fox", "motion": "tail swishes"})):
        _mcp(cls).execute(**args)
        assert calls[-1].method == "POST" and calls[-1].payload["tool_name"] == cls.name


def test_wait_for_result_returns_the_finished_result_or_the_job_to_poll(monkeypatch):
    from backend.tools import image_tools as it

    monkeypatch.setattr(mcp_config, "load_config", lambda: mcp_config.MCPConfig())
    calls = _fake_backend(monkeypatch, status="done")
    res = _mcp(it.EditImageTool).execute(instruction="night", image=IMG, wait_for_result=True)
    assert res.success and EDITED in res.output
    assert calls[-1].path == f"/api/tools/jobs/{JOB}?wait_s=60" and calls[-1].read_timeout > 60

    _fake_backend(monkeypatch, status="running")
    res = _mcp(it.AnimationGeneratorTool).execute(prompt="a fox", motion="m", wait_for_result=True)
    assert res.success and "still running after 60 s" in res.output and f'batch_id="{JOB}"' in res.output


def test_get_generation_status_over_mcp_reads_only_the_tool_job_route(monkeypatch):
    from backend.tools.image_tools import GenerationStatusTool

    calls = _fake_backend(monkeypatch)
    status = _mcp(GenerationStatusTool)
    res = status.execute(batch_id=JOB)
    assert res.success and f"Image edit job {JOB}: done" in res.output
    assert f"File: {EDITED}" in res.output and "Elapsed: 170 s" in res.output
    assert [c.path for c in calls] == [f"/api/tools/jobs/{JOB}"]
    res = status.execute(batch_id="tooljob_000000_fedcba987654")
    assert not res.success and "restarted" in res.error


# ---- what MCP clients are told --------------------------------------------------------------
@pytest.mark.parametrize("name", ["edit_image", "inpaint_image", "outpaint_image", "remove_background",
                                  "generate_animation"])
def test_each_job_tool_declares_wait_for_result_and_how_to_poll(name):
    from backend.tools import image_tools as it

    cls = {c.name: c for c in (it.EditImageTool, it.InpaintImageTool, it.OutpaintImageTool,
                               it.RemoveBackgroundTool, it.AnimationGeneratorTool)}[name]
    param = cls.parameters["wait_for_result"]
    assert param.type == "bool" and param.default is False and not param.required
    assert "get_generation_status" in cls.description and "wait_for_result" in cls.description
    assert "tooljob_" in it.GenerationStatusTool.description
    # inpaint_image is not offered over MCP (DEFAULT_DENY_TOOLS), so the status
    # tool MCP clients read does not name it.
    if name != "inpaint_image":
        assert name in it.GenerationStatusTool.description


def test_the_timeout_text_never_points_at_a_batch_id():
    from backend.mcp.tools_adapter import _timeout_message

    text = _timeout_message("edit_image", 120)
    assert "batch id" not in text and "no job id" in text and "second run" in text
