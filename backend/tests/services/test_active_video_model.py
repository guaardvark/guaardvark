"""resolve_active_video_model: explicit > surface > global > hardware fallback."""
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
