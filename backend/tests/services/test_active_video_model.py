"""resolve_active_video_model: explicit > surface > global > hardware fallback."""
import pytest

from backend.services import video_model_registry as vmr


def _always_ready(monkeypatch):
    monkeypatch.setattr(vmr, "preflight_video_model", lambda m: (True, ""))


def test_explicit_id_wins(monkeypatch):
    _always_ready(monkeypatch)
    mid, err = vmr.resolve_active_video_model("t2v", "wan22-5b")
    assert err is None and mid == "wan22-5b"


def test_explicit_wrong_role_is_refused(monkeypatch):
    _always_ready(monkeypatch)
    mid, err = vmr.resolve_active_video_model("i2v", "wan22-14b")
    assert mid is None and err and "cannot serve" in err


def test_unknown_explicit_is_refused():
    mid, err = vmr.resolve_active_video_model("t2v", "not-a-model")
    assert mid is None and err and "Unknown" in err


def test_surface_override(monkeypatch):
    _always_ready(monkeypatch)
    monkeypatch.setattr(
        vmr, "_video_setting",
        lambda k: "wan22-5b" if k == "active_video_model_film_crew" else "",
    )
    mid, err = vmr.resolve_active_video_model("i2v", surface="film-crew")
    assert err is None and mid == "wan22-5b"


def test_global_t2v_uses_i2v_sibling(monkeypatch):
    _always_ready(monkeypatch)
    monkeypatch.setattr(
        vmr, "_video_setting",
        lambda k: "wan22-14b" if k == "active_video_model" else "",
    )
    mid, err = vmr.resolve_active_video_model("i2v")
    assert err is None and mid == "wan22-14b-i2v"


def test_ti2v_global_serves_i2v(monkeypatch):
    _always_ready(monkeypatch)
    monkeypatch.setattr(
        vmr, "_video_setting",
        lambda k: "wan22-5b" if k == "active_video_model" else "",
    )
    mid, err = vmr.resolve_active_video_model("i2v")
    assert err is None and mid == "wan22-5b"


def test_hardware_prefers_compile_time_default_when_installed(monkeypatch):
    _always_ready(monkeypatch)
    monkeypatch.setattr(vmr, "_video_setting", lambda k: "")
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: m == "wan22-5b")
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: 16376)
    mid, err = vmr.resolve_active_video_model("t2v")
    assert err is None and mid == "wan22-5b"


def test_unfit_model_is_not_the_automatic_default(monkeypatch):
    monkeypatch.setattr(vmr, "_video_setting", lambda k: "")
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: m == "minimax-h3-int8")
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: 8192)
    mid, err = vmr.resolve_active_video_model("t2v")
    assert mid is None and err and "No installed" in err


def test_empty_overrides_inherit(monkeypatch):
    _always_ready(monkeypatch)
    monkeypatch.setattr(
        vmr, "_video_setting",
        lambda k: "wan22-5b" if k == "active_video_model" else "",
    )
    mid, err = vmr.resolve_active_video_model("t2v", surface="music-video")
    assert err is None and mid == "wan22-5b"


def test_clip_defaults_are_native_not_svd():
    d = vmr.clip_defaults_for("wan22-5b")
    assert d["fps"] == 24
    assert d["duration_frames"] <= 121
    assert d["num_inference_steps"] >= 20
    assert d["width"] >= 256 and d["height"] >= 256
    d14 = vmr.clip_defaults_for("wan22-14b")
    assert d14["fps"] == 16
    assert d14["duration_frames"] <= 81


def test_hardware_fallback_takes_the_largest_model_that_fits(monkeypatch):
    """Two installed families, no setting: the card gets the bigger one, not the
    one the registry happens to list first."""
    _always_ready(monkeypatch)
    monkeypatch.setattr(vmr, "_video_setting", lambda k: "")
    installed = {"hunyuan-t2v", "ltx23-distilled-fp8"}
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: m in installed)
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: 24576)
    candidates = [m for m in installed if vmr._role_ok(m, "t2v")]
    assert len(candidates) == 2, candidates
    mid, err = vmr.resolve_active_video_model("t2v")
    assert err is None
    assert mid == max(candidates, key=vmr.vram_mb_for_model)


def test_hardware_fallback_skips_models_that_do_not_fit(monkeypatch):
    _always_ready(monkeypatch)
    monkeypatch.setattr(vmr, "_video_setting", lambda k: "")
    installed = {"hunyuan-t2v", "ltx23-distilled-fp8"}
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: m in installed)
    small = min(vmr.vram_mb_for_model(m) for m in installed)
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: small + 1024)
    mid, err = vmr.resolve_active_video_model("t2v")
    assert err is None
    assert vmr.vram_mb_for_model(mid) == small


@pytest.mark.parametrize("model,frames,expect_down,expect_up", [
    ("wan22-5b", 30, 29, 33),              # 4n+1
    ("ltx23-distilled-fp8", 30, 25, 33),   # 8n+1
    ("minimax-h3-int8", 30, 22, 39),       # 17k+5
])
def test_snap_frames_follows_each_declared_grid(model, frames, expect_down, expect_up):
    assert vmr.snap_frames(model, frames) == expect_down
    assert vmr.snap_frames(model, frames, up=True) == expect_up


def test_snap_frames_leaves_unruled_models_alone(monkeypatch):
    monkeypatch.setattr(vmr, "model_capabilities", lambda m: {"frame_rule": None})
    assert vmr.snap_frames("anything", 31) == 31


def test_a_stopped_comfyui_is_accepted_only_where_the_caller_starts_it(monkeypatch):
    """Video Gen's routes start ComfyUI after resolving; music video and Film Crew do not."""
    from backend.services.job_types import RenderErrorKind, RenderFailure
    down = RenderFailure(RenderErrorKind.COMFYUI_DOWN, "requires ComfyUI. Start the ComfyUI plugin, then retry.")
    missing = RenderFailure(RenderErrorKind.MODEL_NOT_INSTALLED, "is not installed.")
    monkeypatch.setattr(vmr, "preflight_video_model", lambda m: (False, down))
    assert vmr.resolve_active_video_model("t2v", "minimax-h3-int8", comfyui_down_ok=True) == ("minimax-h3-int8", None)
    assert vmr.resolve_active_video_model("t2v", "minimax-h3-int8") == (None, down)
    monkeypatch.setattr(vmr, "preflight_video_model", lambda m: (False, missing))
    assert vmr.resolve_active_video_model("t2v", "minimax-h3-int8", comfyui_down_ok=True) == (None, missing)


# ── The automatic pick: preflight, an unread card, and no family swap ────────

def test_the_hardware_pick_passes_preflight_like_a_typed_one(monkeypatch):
    """A candidate missing a companion is not handed out; the next ready one is."""
    from backend.services.job_types import RenderErrorKind, RenderFailure
    monkeypatch.setattr(vmr, "_video_setting", lambda k: "")
    installed = {"hunyuan-t2v", "ltx23-distilled-fp8"}
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: m in installed)
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: 24576)
    biggest = max(installed, key=vmr.vram_mb_for_model)
    other = (installed - {biggest}).pop()
    broken = RenderFailure(RenderErrorKind.COMPANION_MISSING, "is missing companion 'VAE'.")
    checked = []

    def preflight(m):
        checked.append(m)
        return (False, broken) if m == biggest else (True, "")

    monkeypatch.setattr(vmr, "preflight_video_model", preflight)
    assert vmr.resolve_active_video_model("t2v") == (other, None)
    assert checked == [biggest, other]

    monkeypatch.setattr(vmr, "preflight_video_model", lambda m: (False, broken))
    assert vmr.resolve_active_video_model("t2v") == (None, broken)


def test_an_unread_card_fits_nothing(monkeypatch):
    _always_ready(monkeypatch)
    monkeypatch.setattr(vmr, "_video_setting", lambda k: "")
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: True)
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: 0)
    monkeypatch.setattr(vmr, "_no_gpu_hardware", lambda: False)
    assert vmr._fits_card("wan22-5b", 0) is False
    assert vmr._fits_card("wan22-5b", None) is False
    mid, err = vmr.resolve_active_video_model("t2v")
    assert mid is None and "could not be read" in err


def test_a_host_without_a_gpu_is_told_video_needs_one(monkeypatch):
    _always_ready(monkeypatch)
    monkeypatch.setattr(vmr, "_video_setting", lambda k: "")
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: 0)
    monkeypatch.setattr(vmr, "_no_gpu_hardware", lambda: True)
    mid, err = vmr.resolve_active_video_model("t2v")
    assert mid is None and err == vmr.NO_VIDEO_GPU_MESSAGE


def test_a_t2v_job_under_an_i2v_only_global_stays_in_its_family(monkeypatch):
    _always_ready(monkeypatch)
    monkeypatch.setattr(
        vmr, "_video_setting",
        lambda k: "hunyuan-i2v" if k == "active_video_model" else "",
    )
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: True)
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: 24576)
    assert vmr.resolve_active_video_model("t2v") == ("hunyuan-t2v", None)


def test_a_global_whose_family_cannot_serve_the_role_is_refused_not_swapped(monkeypatch):
    """No same-family text-to-video model: refuse with the reason rather than
    hand the job to whatever the hardware pick would choose."""
    _always_ready(monkeypatch)
    monkeypatch.delitem(vmr.VIDEO_MODEL_REGISTRY, "hunyuan-t2v")
    monkeypatch.setattr(
        vmr, "_video_setting",
        lambda k: "hunyuan-i2v" if k == "active_video_model" else "",
    )
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: True)
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: 24576)
    assert vmr._hardware_candidates("t2v", 24576), "the hardware pick would have had a model"
    mid, err = vmr.resolve_active_video_model("t2v")
    assert mid is None
    assert "HunyuanVideo 13B I2V" in err and "cannot make video from text alone" in err


def test_an_i2v_job_under_a_t2v_global_without_a_sibling_is_refused(monkeypatch):
    _always_ready(monkeypatch)
    monkeypatch.delitem(vmr.VIDEO_MODEL_REGISTRY, "hunyuan-i2v")
    monkeypatch.setattr(
        vmr, "_video_setting",
        lambda k: "hunyuan-t2v" if k == "active_video_model" else "",
    )
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: True)
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: 24576)
    mid, err = vmr.resolve_active_video_model("i2v")
    assert mid is None and "cannot make video from a start image" in err
