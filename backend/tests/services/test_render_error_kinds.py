"""Every way a video render fails, named once (job_types.RenderErrorKind) and
read the same way by the batch status JSON, the MCP text and the Jobs page.

Each kind is produced by the real generate_video (or the real preflight) with
ComfyUI's replies scripted at the HTTP seam (tests/fixtures/workflow_contract.py
FakeComfyUI), the way ComfyUI v0.34.0 sends them (execution.py).
"""
import json

import numpy as np
import pytest

from backend.services import comfyui_video_generator as cvg
from backend.services.job_types import (
    RENDER_ERROR_POLICY, RenderErrorKind as K, RenderFailure, batch_failure, describe_failure,
    failure_kind, failure_text, render_failed, retry_waits,
)
from backend.tests.fixtures import workflow_contract as wc
from backend.tests.fixtures.workflow_contract import comfy  # noqa: F401 — fixture

MODEL = "wan22-5b"   # a ComfyUI graph that fits the fake 16 GB card without GGUF


@pytest.fixture(autouse=True)
def _no_verbatim_lookup(monkeypatch):
    from backend.services import media_director
    monkeypatch.setattr(media_director, "verbatim_prompts_enabled", lambda: False)


def _render(comfy, **fields):
    fields.setdefault("model", MODEL)
    fields.setdefault("prompt", "a red fox in snow")
    fields.setdefault("width", 832)
    fields.setdefault("height", 480)
    fields.setdefault("duration_frames", 49)
    fields.setdefault("interpolation_multiplier", 1)
    result, _, _ = comfy.render(**fields)
    return result


def _clip(frames) -> bytes:
    """An H.264 MP4 of these RGB frames, as VHS_VideoCombine would write it."""
    import tempfile
    from pathlib import Path

    from backend.tests.fixtures.video_quality_clips import write_clip

    with tempfile.TemporaryDirectory() as d:
        return write_clip(Path(d) / "c.mp4", frames).read_bytes()


def _execution_error(exception_type, message, node_type="KSampler", node_id="10"):
    return {"status": {"status_str": "error", "completed": False, "messages": [
        ["execution_start", {"prompt_id": "p"}],
        ["execution_error", {"prompt_id": "p", "node_id": node_id, "node_type": node_type,
                             "exception_type": exception_type, "exception_message": message,
                             "traceback": [], "current_inputs": {}, "current_outputs": []}],
    ]}}


# ── The vocabulary itself ────────────────────────────────────────────────────

def test_every_kind_has_a_policy():
    assert set(RENDER_ERROR_POLICY) == set(K)
    for kind, policy in RENDER_ERROR_POLICY.items():
        assert set(policy) == {"label", "retryable", "retry_after_s", "action"}, kind
        assert policy["retryable"] == bool(policy["retry_after_s"]), kind
        assert policy["label"], kind


def test_retryable_kinds_are_the_transient_ones():
    assert {k for k in K if retry_waits(k)} == {K.COMFYUI_DOWN, K.VRAM_BUSY, K.RENDER_TIMEOUT, K.OUTPUT_MISSING}


def test_a_failure_message_carries_its_kind_through_string_handling():
    msg = RenderFailure(K.COMPANION_MISSING, "Lightning LoRA is not installed.")
    assert msg == "Lightning LoRA is not installed." and failure_kind(msg) is K.COMPANION_MISSING
    assert failure_kind(RuntimeError(msg).args[0]) is K.COMPANION_MISSING
    assert failure_kind("plain text") is None and failure_kind("plain", K.UNKNOWN) is K.UNKNOWN
    wrapped = render_failed("Wan 2.2 I2V", K.OOM, "Allocation failed")
    assert failure_kind(wrapped) is K.OOM
    assert str(wrapped).startswith("Wan 2.2 I2V failed — Out of GPU memory: Allocation failed Next: Lower")


def test_failure_text_says_what_to_do_and_whether_to_retry():
    assert failure_text(K.VRAM_BUSY, "the card is held") == (
        "GPU busy: the card is held Next: Another job holds the GPU; retry when it finishes. "
        "(Retrying later can succeed.)")
    assert failure_text(None, "boom").startswith("Render failed: boom Next:")


# ── One test per kind, through generate_video ────────────────────────────────

def test_comfyui_down_before_the_render(comfy):
    comfy.fake.alive = False
    comfy.gen.service_available = False
    result = _render(comfy)
    assert (result.success, result.error_kind) == (False, "comfyui_down")


def test_comfyui_down_is_not_reported_as_a_missing_node(comfy):
    # The cached flag still says available; ComfyUI has gone away since.
    comfy.fake.alive = False
    result = _render(comfy)
    assert result.error_kind == "comfyui_down" and "VHS_VideoCombine" not in result.error


def test_comfyui_dies_during_the_render(comfy, monkeypatch):
    monkeypatch.setattr(cvg, "DEAD_PROBE_LIMIT", 1)
    comfy.fake.die_after_prompt = True
    result = _render(comfy)
    assert result.error_kind == "comfyui_down" and "stopped answering" in result.error


def test_node_missing_from_object_info(comfy):
    comfy.fake.info = {k: v for k, v in comfy.fake.info.items() if k != "VHS_VideoCombine"}
    result = _render(comfy)
    assert result.error_kind == "node_missing" and "install_deps.sh" in result.error


def test_node_missing_in_a_prompt_refusal(comfy):
    comfy.fake.prompt_reply = (400, {"error": {
        "type": "missing_node_type", "message": "Node 'RIFE VFI' not found. The custom node may not be installed.",
        "details": "Node ID '#20'", "extra_info": {"node_id": "20", "class_type": "RIFE VFI"}}, "node_errors": {}})
    result = _render(comfy)
    assert result.error_kind == "node_missing" and "RIFE VFI" in result.error


def test_model_file_missing_in_a_prompt_refusal(comfy):
    comfy.fake.prompt_reply = (400, {
        "error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation",
                  "details": "", "extra_info": {}},
        "node_errors": {"1": {"class_type": "UNETLoader", "dependent_outputs": ["13"], "errors": [{
            "type": "value_not_in_list", "message": "Value not in list",
            "details": "unet_name: 'wan2.2_ti2v_5B_fp16.safetensors' not in []",
            "extra_info": {"input_name": "unet_name", "received_value": "wan2.2_ti2v_5B_fp16.safetensors"}}]}}})
    result = _render(comfy)
    assert result.error_kind == "model_not_installed" and "unet_name" in result.error


def test_other_prompt_refusals_are_node_errors(comfy):
    comfy.fake.prompt_reply = (400, {
        "error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
        "node_errors": {"10": {"class_type": "KSampler", "errors": [{
            "type": "value_bigger_than_max", "message": "Value 200 bigger than max of 100",
            "details": "steps", "extra_info": {"input_name": "steps"}}]}}})
    assert _render(comfy).error_kind == "node_error"


def test_oom_inside_comfyui(comfy):
    comfy.fake.history_entry = _execution_error(
        "torch.OutOfMemoryError",
        "Allocation on device \nThis error means you ran out of memory on your GPU.\n\nTIPS: ...")
    result = _render(comfy)
    assert result.error_kind == "oom"
    assert result.error == "ComfyUI ran out of GPU memory in KSampler (node 10): Allocation on device"


def test_node_error_names_the_node(comfy):
    comfy.fake.history_entry = _execution_error(
        "RuntimeError", "mat1 and mat2 shapes cannot be multiplied (77x768 and 4096x5120)",
        node_type="WanImageToVideo", node_id="7")
    result = _render(comfy)
    assert result.error_kind == "node_error"
    assert result.error.startswith("ComfyUI failed in WanImageToVideo (node 7): mat1 and mat2 shapes")


def test_interrupt_is_a_cancel(comfy):
    comfy.fake.history_entry = {"status": {"status_str": "error", "completed": False, "messages": [
        ["execution_interrupted", {"prompt_id": "p", "node_id": "10", "node_type": "KSampler", "executed": []}]]}}
    assert _render(comfy).error_kind == "cancelled"


class _Clock:
    """cvg.time as the wait loop reads it: every look at the clock is an hour on."""

    def __init__(self):
        import time as _time
        self._now = _time.time()

    def time(self):
        self._now += 3600
        return self._now

    def sleep(self, s):
        pass


def test_a_render_still_running_at_the_ceiling_times_out(comfy, monkeypatch):
    comfy.fake.history_entry = {"status": {"status_str": "running", "completed": False, "messages": []}}
    comfy.fake.running = True
    monkeypatch.setattr(cvg, "time", _Clock())
    result = _render(comfy)
    assert result.error_kind == "render_timeout" and "had not finished" in result.error


def test_a_prompt_that_leaves_the_queue_without_a_result_times_out(comfy, monkeypatch):
    comfy.fake.history_entry = {"status": {"status_str": "running", "completed": False, "messages": []}}
    monkeypatch.setattr(cvg, "time", _Clock())
    result = _render(comfy)
    assert result.error_kind == "render_timeout" and "dropped the render" in result.error


def test_output_missing_when_the_file_is_not_served(comfy):
    comfy.fake.finish_with({})
    comfy.fake.history_entry["outputs"]["13"]["gifs"] = [{"filename": "gone.mp4", "subfolder": "", "type": "output"}]
    result = _render(comfy)
    assert result.error_kind == "output_missing"


def test_blank_output_a_stub_file(comfy):
    comfy.fake.finish_with({"wan_00001.mp4": b"\x00" * 200})
    result = _render(comfy)
    assert result.error_kind == "blank_output" and "200 bytes" in result.error


def test_blank_output_an_all_black_clip_without_ffmpeg(comfy, monkeypatch):
    monkeypatch.setenv("PATH", "")   # no ffmpeg binary: the PyAV checker decides
    rng = np.random.default_rng(0)
    black = _clip(rng.integers(0, 4, (192, 320, 3), dtype=np.uint8) for _ in range(33))
    assert len(black) > 10 * 1024
    comfy.fake.finish_with({"wan_00001.mp4": black})
    result = _render(comfy)
    assert result.error_kind == "blank_output" and "black in all" in result.error


def test_a_real_clip_succeeds_without_a_kind(comfy, monkeypatch):
    monkeypatch.setenv("PATH", "")
    from backend.tests.fixtures.video_quality_clips import FRAMES, _scene
    comfy.fake.finish_with({"wan_00001.mp4": _clip(_scene(i) for i in range(FRAMES))})
    result = _render(comfy)
    assert result.success and result.error_kind is None and result.video_path


def test_card_too_small(comfy):
    comfy.fake.total_vram_mb = 6 * 1024
    assert _render(comfy).error_kind == "card_too_small"


def test_vram_busy_after_the_wait(comfy, monkeypatch):
    from backend.services import gpu_memory_orchestrator

    class _Full:
        def request_model(self, *a, **k):
            raise RuntimeError("need 11000 MB, 2000 MB free (held by batch_x)")

    monkeypatch.setattr(gpu_memory_orchestrator, "get_orchestrator", lambda: _Full())
    monkeypatch.setenv(cvg.VRAM_WAIT_ENV, "0")
    result = _render(comfy)
    assert result.error_kind == "vram_busy" and "held by batch_x" in result.error


def test_invalid_request(comfy):
    # wan22-14b is text-to-video only.
    result = _render(comfy, model="wan22-14b", metadata={"image_path": comfy.image})
    assert result.error_kind == "invalid_request"


def test_companion_missing_carried_from_the_adapter_check(comfy, monkeypatch):
    from backend.services import video_model_registry as reg
    monkeypatch.setitem(reg.VIDEO_MODEL_REGISTRY, "user-lora-x", {
        "name": "Style X", "type": "lora", "applies_to": [MODEL],
        "files": [{"dst": "style_x.safetensors"}], "local_subdir": "loras"})
    result = _render(comfy, adapters=[{"id": "user-lora-x"}])
    assert result.error_kind == "companion_missing" and "Style X is not installed" in result.error


def test_an_unexpected_exception_is_unknown(comfy, monkeypatch):
    def _boom(*a, **k):
        raise ValueError("builder bug")
    monkeypatch.setattr(cvg.ComfyUIVideoGenerator, "_create_wan22_5b_workflow", _boom)
    result = _render(comfy)
    assert (result.error_kind, result.error) == ("unknown", "builder bug")


# ── Preflight (REST route and MCP tool) ──────────────────────────────────────

def test_preflight_kinds(monkeypatch):
    from backend.services import video_model_registry as reg

    ok, err = reg.preflight_video_model("no-such-model")
    assert not ok and failure_kind(err) is K.INVALID_REQUEST
    monkeypatch.setattr(reg, "is_model_installed", lambda mid: False)
    ok, err = reg.preflight_video_model("wan22-14b")
    assert not ok and failure_kind(err) is K.MODEL_NOT_INSTALLED
    monkeypatch.setattr(reg, "is_model_installed", lambda mid: mid == "hunyuan-t2v")
    ok, err = reg.preflight_video_model("hunyuan-t2v")
    assert not ok and failure_kind(err) is K.COMPANION_MISSING
    monkeypatch.setattr(reg, "is_model_installed", lambda mid: True)
    monkeypatch.setattr(reg, "_comfyui_reachable", lambda: False)
    ok, err = reg.preflight_video_model("wan22-14b")
    assert not ok and failure_kind(err) is K.COMFYUI_DOWN


def test_rest_refusal_carries_the_failure_record(monkeypatch):
    from flask import Flask

    from backend.api import batch_video_generation_api as bva
    from backend.services import video_model_registry as reg

    monkeypatch.setattr(reg, "is_model_installed", lambda mid: False)
    app = Flask(__name__)
    app.register_blueprint(bva.batch_video_bp)
    resp = app.test_client().post("/api/batch-video/generate/text", json={"prompts": ["a fox"], "model": "wan22-14b"})
    assert resp.status_code == 400
    failure = resp.get_json()["error"]["details"]["failure"]
    assert failure["kind"] == "model_not_installed" and failure["action"].startswith("Open Manage Video Models")


def test_mcp_refusal_names_the_kind(monkeypatch):
    from backend.services import video_model_registry as reg
    from backend.tools.image_tools import VideoGeneratorTool

    monkeypatch.setattr(reg, "is_model_installed", lambda mid: False)
    out = VideoGeneratorTool().execute(prompt="a fox", model="wan22-14b")
    assert not out.success and out.error.startswith("Model not installed: ")
    assert out.metadata["failure"]["kind"] == "model_not_installed"


# ── The batch: status JSON, MCP text, Jobs page, retries ─────────────────────

def _batch(comfy, tmp_path, monkeypatch, **params):
    """Run the real batch worker over the real generate_video and the fake ComfyUI."""
    import queue
    import threading

    from flask import Flask

    from backend.services.batch_video_generator import BatchVideoGenerator, BatchVideoItem

    gen = BatchVideoGenerator.__new__(BatchVideoGenerator)
    gen.base_output_dir = tmp_path / "Videos"
    gen.active_batches, gen.cancel_events, gen.queue_order = {}, {}, []
    gen.batch_lock = threading.Lock()
    gen.batch_queue = queue.Queue()
    gen.video_generator = comfy.gen
    params.setdefault("model", MODEL)
    params.setdefault("width", 832)
    params.setdefault("height", 480)
    params.setdefault("duration_frames", 49)
    params.setdefault("interpolation_multiplier", 1)
    params.setdefault("enhance_prompt", False)
    status = gen._start_batch(batch_id="b1", items=[BatchVideoItem(id="i1", prompt="a fox")], **params)
    request, _ = gen.batch_queue.get_nowait()
    monkeypatch.chdir(tmp_path)
    with Flask(__name__).app_context():
        gen._run_batch_inner(request, status)
    return gen, status


def test_batch_status_json_and_mcp_text_read_the_kind(comfy, tmp_path, monkeypatch):
    from flask import Flask

    from backend.api import batch_video_generation_api as bva
    from backend.tools import image_tools

    comfy.fake.history_entry = _execution_error("torch.OutOfMemoryError", "Allocation on device")
    gen, status = _batch(comfy, tmp_path, monkeypatch)
    assert status.status == "error" and status.results[0].error_kind == "oom"

    monkeypatch.setattr(bva, "get_batch_video_generator", lambda: gen)
    app = Flask(__name__)
    app.register_blueprint(bva.batch_video_bp)
    body = app.test_client().get("/api/batch-video/status/b1").get_json()["data"]
    assert body["results"][0]["error_kind"] == "oom"
    assert body["results"][0]["failure"]["label"] == "Out of GPU memory"
    assert body["failure"]["kind"] == "oom" and body["failure"]["retryable"] is False

    # The saved record reads back with its kind (a restart, or the Jobs page).
    saved = json.loads((tmp_path / "Videos" / "b1" / "batch_metadata.json").read_text())
    assert saved["results"][0]["error_kind"] == "oom"
    from backend.services.job_registry import adapt_video_gen
    job = adapt_video_gen(saved).to_dict()
    assert job["error_message"].startswith("Out of GPU memory: ComfyUI ran out of GPU memory")
    assert job["metadata"]["failure"]["kind"] == "oom"

    # MCP over HTTP reads the same record.
    monkeypatch.setattr(image_tools, "_http_json", lambda method, path, *a, **k: (
        body if path.startswith("/api/batch-video/status/") else (_ for _ in ()).throw(RuntimeError("404 not found"))))
    tool = image_tools.GenerationStatusTool()
    tool._context = {"transport": "mcp"}
    out = tool.execute(batch_id="b1")
    assert "Failed item: Out of GPU memory: ComfyUI ran out of GPU memory in KSampler (node 10)" in out.output
    assert "Next: Lower the size" in out.output
    assert out.metadata["failures"][0]["kind"] == "oom"


def test_cancel_before_start_is_a_cancel(comfy, tmp_path, monkeypatch):
    import threading

    from backend.services.batch_video_generator import BatchVideoGenerator

    event = threading.Event()
    event.set()
    monkeypatch.setattr(BatchVideoGenerator, "_render_with_retries",
                        lambda self, *a: pytest.fail("a cancelled batch must not render"))
    orig = BatchVideoGenerator._start_batch

    def _start(self, **kw):
        status = orig(self, **kw)
        self.cancel_events["b1"] = event
        return status

    monkeypatch.setattr(BatchVideoGenerator, "_start_batch", _start)
    _, status = _batch(comfy, tmp_path, monkeypatch)
    assert batch_failure(status)["kind"] == "cancelled"


def test_retries_are_off_by_default(comfy, tmp_path, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_VIDEO_AUTO_RETRY", raising=False)
    comfy.fake.finish_with({})
    comfy.fake.history_entry["outputs"]["13"]["gifs"] = [{"filename": "gone.mp4", "type": "output"}]
    _, status = _batch(comfy, tmp_path, monkeypatch)
    assert status.results[0].error_kind == "output_missing" and len(comfy.fake.prompts) == 1


def test_a_retryable_kind_is_rendered_again_from_a_fresh_request(comfy, tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_VIDEO_AUTO_RETRY", "1")
    monkeypatch.setitem(RENDER_ERROR_POLICY[K.OUTPUT_MISSING], "retry_after_s", [0])
    comfy.fake.finish_with({})
    comfy.fake.history_entry["outputs"]["13"]["gifs"] = [{"filename": "gone.mp4", "type": "output"}]
    _, status = _batch(comfy, tmp_path, monkeypatch, enhance_prompt=True, prompt_style="anime")
    assert len(comfy.fake.prompts) == 2
    first, second = (wc.encoded_text(p, wc.one(p, "KSampler")[1]["inputs"]["positive"]) for p in comfy.fake.prompts)
    assert first == second and first.count("Anime style") == 1   # enhanced once per attempt
    assert status.results[0].metadata["render_attempts"] == 2


def test_a_non_retryable_kind_is_not_retried(comfy, tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_VIDEO_AUTO_RETRY", "1")
    comfy.fake.history_entry = _execution_error("torch.OutOfMemoryError", "Allocation on device")
    _, status = _batch(comfy, tmp_path, monkeypatch)
    assert status.results[0].error_kind == "oom" and len(comfy.fake.prompts) == 1


def test_pipeline_failures_keep_the_kind():
    from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError, classify_render_exception

    assert classify_render_exception(RuntimeError(render_failed("Wan 2.2 I2V", K.NODE_ERROR, "x"))) is K.NODE_ERROR
    assert classify_render_exception(GpuCapacityError("never fits")) is K.CARD_TOO_SMALL
    assert classify_render_exception(GpuBusyError("held")) is K.VRAM_BUSY
    assert classify_render_exception(RuntimeError("CUDA out of memory. Tried to allocate")) is K.OOM
    assert classify_render_exception(ValueError("x")) is K.UNKNOWN
    assert describe_failure(None, "x")["kind"] == "unknown"
