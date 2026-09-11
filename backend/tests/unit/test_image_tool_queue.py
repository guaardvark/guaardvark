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
            self.metadata = {"model_used": "Z-Image"}
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
    assert "Failed item: oom" in result.output


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
        def request(method, url, json=None, timeout=None):
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
                         "metadata": {"model_used": "Z-Image"}, "generation_time": 12.5}]}}

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
