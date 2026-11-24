"""generate_image with wait_for_result=False queues one prompt on the batch
image generator and returns the batch id; get_generation_status reads a
batch of either kind back. No GPU, no app: the services are monkeypatched at
the seams the tool imports."""
import sys
import types

from backend.tools.image_tools import GenerationStatusTool, ImageGeneratorTool


def _fake_batch_module(monkeypatch, start=None, status=None):
    mod = types.ModuleType("backend.services.batch_image_generator")
    calls = {}

    def start_batch_from_prompts(prompts, **kwargs):
        calls["prompts"] = prompts
        calls["kwargs"] = kwargs
        return "ImageBatch_01-01-2026_000000_001"

    class _Gen:
        def find_batch_status(self, batch_id, *, include_results=False):
            return status

    mod.start_batch_from_prompts = start or start_batch_from_prompts
    mod.get_batch_image_generator = lambda: _Gen()
    monkeypatch.setitem(sys.modules, "backend.services.batch_image_generator", mod)
    return calls


def test_queued_mode_returns_batch_id_without_rendering(monkeypatch):
    calls = _fake_batch_module(monkeypatch)
    # The inline path must not be reached: make it explode if it is.
    stills = types.ModuleType("backend.services.stills_pipeline")
    stills.run_stills_pipeline = lambda *a, **k: (_ for _ in ()).throw(AssertionError("rendered inline"))
    monkeypatch.setitem(sys.modules, "backend.services.stills_pipeline", stills)
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda prompt: [])

    result = ImageGeneratorTool().execute(
        prompt="a brass key", width=768, height=768, style="artistic",
        model="zimage-turbo", wait_for_result=False, negative_prompt="blur",
    )
    assert result.success
    assert result.metadata["queued"] is True
    assert result.metadata["batch_id"] == "ImageBatch_01-01-2026_000000_001"
    assert "get_generation_status" in result.output
    assert calls["prompts"] == ["a brass key"]
    assert calls["kwargs"]["width"] == 768 and calls["kwargs"]["height"] == 768
    assert calls["kwargs"]["model"] == "zimage-turbo"
    assert calls["kwargs"]["style"] == "artistic"
    assert calls["kwargs"]["negative_prompt"] == "blur"
    assert "subject_ids" not in calls["kwargs"]


def test_queued_mode_carries_cast_subject_ids(monkeypatch):
    calls = _fake_batch_module(monkeypatch)
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda prompt: [])
    result = ImageGeneratorTool().execute(prompt="mara at the pier", subject_ids=[26], wait_for_result="false")
    assert result.success and calls["kwargs"]["subject_ids"] == [26]
    assert "Cast LoRA: ON" in result.output


def test_queue_failure_is_reported_not_raised(monkeypatch):
    def _boom(prompts, **kwargs):
        raise RuntimeError("service down")
    _fake_batch_module(monkeypatch, start=_boom)
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda prompt: [])
    result = ImageGeneratorTool().execute(prompt="x", wait_for_result=False)
    assert not result.success and "service down" in result.error


def test_status_tool_reads_an_image_batch(monkeypatch):
    class _R:
        def __init__(self, ok, path=None, err=None):
            self.success, self.image_path, self.error = ok, path, err
            self.metadata = {"model_used": "Z-Image", "steps": 8, "steps_requested": 4,
                             "steps_notice": "Raised sampling to 8 steps."}
            self.generation_time = 20.6

    class _S:
        status = "completed"
        completed_images, failed_images, total_images = 1, 1, 2
        error = None
        results = [_R(True, "/data/ImageBatch_x/images/img_1.png"), _R(False, err="oom")]

    _fake_batch_module(monkeypatch, status=_S())
    result = GenerationStatusTool().execute(batch_id="ImageBatch_x")
    assert result.success
    assert result.metadata["kind"] == "image"
    assert result.metadata["files"][0]["url"] == "/api/batch-image/image/ImageBatch_x/img_1.png"
    assert "1/2 finished, 1 failed" in result.output
    # An image failure with no kind of its own reads as the vocabulary's unknown kind.
    assert "Failed item: Render failed: oom" in result.output
    assert "Steps: 8" in result.output
    assert "Raised sampling to 8 steps." in result.output


def test_status_tool_falls_through_to_video(monkeypatch):
    _fake_batch_module(monkeypatch, status=None)

    class _R:
        success, video_path, thumbnail_path, error = True, "clip.mp4", "thumb.jpg", None

    class _S:
        status, stage, total_videos, error = "running", "decode", 1, None
        results = [_R()]

    vmod = types.ModuleType("backend.services.batch_video_generator")

    class _VGen:
        def get_batch_status(self, batch_id):
            return _S()

    vmod.get_batch_video_generator = lambda: _VGen()
    monkeypatch.setitem(sys.modules, "backend.services.batch_video_generator", vmod)
    result = GenerationStatusTool().execute(batch_id="vid_1")
    assert result.success and result.metadata["kind"] == "video"
    assert result.metadata["files"][0]["url"] == "/api/batch-video/video/vid_1/clip.mp4"
    assert "Still running" in result.output


def test_status_tool_unknown_batch(monkeypatch):
    _fake_batch_module(monkeypatch, status=None)
    vmod = types.ModuleType("backend.services.batch_video_generator")

    class _VGen:
        def get_batch_status(self, batch_id):
            return None

    vmod.get_batch_video_generator = lambda: _VGen()
    monkeypatch.setitem(sys.modules, "backend.services.batch_video_generator", vmod)
    result = GenerationStatusTool().execute(batch_id="nope")
    assert not result.success and "nope" in result.error


def test_default_stays_inline_for_chat(monkeypatch):
    """Chat renders the image inline: with no wait flag the batch queue is untouched."""
    calls = _fake_batch_module(monkeypatch)
    stills = types.ModuleType("backend.services.stills_pipeline")

    class _Still:
        success, image_path, image_url = False, None, None
        error = "no gpu in tests"
        metadata = {}

    stills.run_stills_pipeline = lambda *a, **k: [_Still()]
    monkeypatch.setitem(sys.modules, "backend.services.stills_pipeline", stills)
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda prompt: [])
    result = ImageGeneratorTool().execute(prompt="x")
    assert "prompts" not in calls
    assert not result.success and "no gpu in tests" in result.error


# ---------------------------------------------------------------------------
# MCP transport: the tool runs outside the backend, so it must go over HTTP
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code, self._body, self.text = status_code, body, str(body)

    def json(self):
        return self._body


def _fake_requests(monkeypatch, routes):
    """routes: {(method, path): (status, body) | callable(payload)->(status, body)}"""
    calls = []

    class _Req:
        @staticmethod
        def request(method, url, json=None, timeout=None, **kwargs):
            path = url.split("127.0.0.1:5000", 1)[1] if "127.0.0.1:5000" in url else url
            calls.append((method, path, json))
            handler = routes[(method, path.split("?")[0])]
            status, body = handler(json) if callable(handler) else handler
            return _FakeResp(status, body)

    monkeypatch.setitem(sys.modules, "requests", _Req)
    monkeypatch.setenv("GUAARDVARK_URL", "http://127.0.0.1:5000")
    return calls


def test_mcp_transport_queues_over_http_and_never_imports_the_generator(monkeypatch):
    calls = _fake_requests(monkeypatch, {
        ("POST", "/api/batch-image/generate/prompts"): (200, {"success": True, "data": {"batch_id": "ImageBatch_http_1"}}),
    })
    # If the in-process generator were touched, this module would explode.
    bad = types.ModuleType("backend.services.batch_image_generator")
    bad.start_batch_from_prompts = lambda *a, **k: (_ for _ in ()).throw(AssertionError("in-process render"))
    monkeypatch.setitem(sys.modules, "backend.services.batch_image_generator", bad)
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda prompt: [])

    tool = ImageGeneratorTool()
    tool.set_context({"transport": "mcp"})
    result = tool.execute(prompt="a teapot", wait_for_result=False, width=768, height=768)
    assert result.success and result.metadata["batch_id"] == "ImageBatch_http_1"
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/api/batch-image/generate/prompts")
    assert payload["prompts"] == ["a teapot"] and payload["width"] == 768


def test_mcp_transport_with_wait_polls_http_and_returns_the_file(monkeypatch):
    polls = {"n": 0}

    def status(_payload):
        polls["n"] += 1
        if polls["n"] < 2:
            return 200, {"success": True, "data": {"status": "running", "total_images": 1, "completed_images": 0, "results": []}}
        return 200, {"success": True, "data": {
            "status": "completed", "total_images": 1, "completed_images": 1, "failed_images": 0,
            "results": [{"success": True, "image_path": "/x/ImageBatch_http_2/images/img.png",
                         "metadata": {"model_used": "Z-Image", "steps": 8, "steps_requested": 4,
                                      "steps_notice": "Raised sampling to 8 steps."}, "generation_time": 12.5}]}}

    _fake_requests(monkeypatch, {
        ("POST", "/api/batch-image/generate/prompts"): (200, {"success": True, "data": {"batch_id": "ImageBatch_http_2"}}),
        ("GET", "/api/batch-image/status/ImageBatch_http_2"): status,
    })
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda prompt: [])
    monkeypatch.setattr(ImageGeneratorTool, "POLL_INTERVAL_S", 0.0)
    tool = ImageGeneratorTool()
    tool.set_context({"transport": "mcp"})
    result = tool.execute(prompt="a teapot", wait_for_result=True)
    assert result.success
    assert result.metadata["image_url"] == "/api/batch-image/image/ImageBatch_http_2/img.png"
    assert "12.5s" in result.output and polls["n"] == 2
    assert "Steps: 8" in result.output
    assert "Raised sampling to 8 steps." in result.output
    assert result.metadata["steps_requested"] == 4


def test_mcp_transport_reports_plugin_offline(monkeypatch):
    _fake_requests(monkeypatch, {
        ("POST", "/api/batch-image/generate/prompts"): (503, {"error": "Batch image generation service not available"}),
    })
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda prompt: [])
    tool = ImageGeneratorTool()
    tool.set_context({"transport": "mcp"})
    result = tool.execute(prompt="x", wait_for_result=False)
    assert not result.success and "not available" in result.error and "plugins" in result.error


def test_status_tool_over_http_reads_image_then_video(monkeypatch):
    _fake_requests(monkeypatch, {
        ("GET", "/api/batch-image/status/vid_9"): (404, {"error": "Batch not found"}),
        ("GET", "/api/batch-video/status/vid_9"): (200, {"success": True, "data": {
            "status": "completed", "stage": None, "total_videos": 1, "completed_videos": 1,
            "results": [{"success": True, "video_path": "clip.mp4", "thumbnail_path": None, "error": None}]}}),
    })
    tool = GenerationStatusTool()
    tool.set_context({"transport": "mcp"})
    result = tool.execute(batch_id="vid_9")
    assert result.success and result.metadata["kind"] == "video"
    assert result.metadata["files"][0]["url"] == "/api/batch-video/video/vid_9/clip.mp4"


def test_tool_declares_optional_steps():
    param = ImageGeneratorTool.parameters["steps"]
    assert param.type == "int"
    assert not param.required
    assert "minimum" in param.description


def test_queued_steps_are_planned_and_never_explicit(monkeypatch):
    from backend.services.stills_defaults import _FAMILY_DEFAULTS
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 8)
    calls = _fake_batch_module(monkeypatch)
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda _: [])
    result = ImageGeneratorTool().execute(
        prompt="a key", model="zimage-turbo", steps=4, steps_explicit=True, wait_for_result=False,
    )
    assert result.success
    assert "Steps: 8 (planned)" in result.output
    assert "Z-Image Turbo needs at least 8 steps; raised 4 to 8." in result.output
    assert calls["kwargs"]["steps"] == 4
    assert calls["kwargs"]["steps_explicit"] is False


def test_inline_success_reports_steps_and_notice(monkeypatch):
    stills = types.ModuleType("backend.services.stills_pipeline")
    notice = "Z-Image Turbo needs at least 8 steps; raised 4 to 8."
    calls = []

    def render(*args, **kwargs):
        calls.append(kwargs)
        return [types.SimpleNamespace(
            success=True, image_path="key.png", image_url="/images/key.png",
            metadata={"steps_requested": 4, "steps_notice": notice},
            generation_time=1.0, prompt_used="a key", width=1024, height=1024,
            steps=8, guidance=0.0, enhance_mode="none", model_used="zimage-turbo",
            seed_used=1, negative_used="",
        )]

    stills.run_stills_pipeline = render
    monkeypatch.setitem(sys.modules, "backend.services.stills_pipeline", stills)
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda _: [])
    result = ImageGeneratorTool().execute(prompt="a key", steps=4, steps_explicit=True)
    assert result.success
    assert "Steps: 8" in result.output
    assert notice in result.output
    assert result.metadata["steps_requested"] == 4
    assert not calls[0].get("steps_explicit", False)


def test_http_queue_uses_server_sampling(monkeypatch):
    _fake_requests(monkeypatch, {
        ("POST", "/api/batch-image/generate/prompts"): (200, {"success": True, "data": {
            "batch_id": "ImageBatch_http", "parameters": {
                "steps": 10, "steps_requested": 4, "steps_notice": "Raised sampling to 10 steps.",
            },
        }}),
    })
    monkeypatch.setattr("backend.tools.image_tools._resolve_cast_from_prompt", lambda _: [])
    tool = ImageGeneratorTool()
    tool.set_context({"transport": "mcp"})
    result = tool.execute(prompt="a key", steps=4, model="zimage-turbo", wait_for_result=False)
    assert result.success
    assert "Steps: 10 (planned)" in result.output
    assert "Raised sampling to 10 steps." in result.output


def test_mcp_refusal_keeps_its_failure_kind(monkeypatch):
    """Over MCP the prompt goes to POST /generate/prompts; its 409 for a missing
    model names the kind in the error envelope, and the tool result keeps it."""
    from backend.tools import image_tools
    from backend.utils.backend_http import BackendError

    message = "No image model is downloaded yet. Open Settings → Image Models and download one."
    body = {"success": False, "status_code": 409, "error": {
        "code": "MODEL_NOT_INSTALLED", "message": message,
        "context": {"failure": {"kind": "model_not_installed", "message": message}},
    }}

    def refuse(method, path, payload=None, timeout=None):
        raise BackendError("http", message, status=409, body=body)

    monkeypatch.setattr(image_tools, "_http_json", refuse)
    monkeypatch.setattr(image_tools, "_resolve_cast_from_prompt", lambda prompt: [])
    tool = ImageGeneratorTool()
    tool.set_context({"transport": "mcp"})
    result = tool.execute(prompt="a brass key", wait_for_result=False)
    assert not result.success
    assert message in result.error
    assert result.metadata.get("error_kind") == "model_not_installed"
