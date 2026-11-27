"""Image jobs against the RAM this machine has (#445, #446).

A job whose RAM need plus the admission floor is more than all the RAM the
system sees can never be admitted, so it is refused at once with a RAM reason
(not after a 10-minute VRAM wait), and the same answer decides the model cards'
warning, the first-install suggestion and Auto's pick. Under WSL the advice is
the .wslconfig memory= line. The render-time RAM stop scales with total RAM the
way the admission floor does.

Nothing here loads a pipeline or touches a GPU; total RAM is passed in or
patched on system_load_gate.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend.services import gpu_resource_policy as grp
from backend.services import system_load_gate as slg
from backend.services.job_operation_gate import (
    GpuCapacityError,
    SystemLoadBusyError,
    SystemRamCapacityError,
    classify_render_exception,
)
from backend.services.job_types import JobKind, RenderErrorKind


@pytest.fixture(autouse=True)
def _not_wsl(monkeypatch):
    monkeypatch.setattr(slg, "running_under_wsl", lambda: False)
    monkeypatch.delenv("GUAARDVARK_RAM_HARD_MIN_GB", raising=False)
    monkeypatch.delenv("GUAARDVARK_MIN_FREE_RAM_GB", raising=False)


# --- the shared answer -------------------------------------------------------

def test_a_need_fits_when_it_and_the_floor_are_within_total_ram():
    # The reported WSL default: 15 GB seen, floor 1.5 GB.
    assert slg.ram_fits_machine(21.0, 15.0) is False      # Z-Image
    assert slg.ram_fits_machine(6.0, 15.0) is True        # Realistic Vision
    assert slg.ram_fits_machine(10.0, 15.0) is True       # SDXL
    # memory=24GB: 21 + 2.4 = 23.4 fits; one GB less does not.
    assert slg.ram_fits_machine(21.0, 24.0) is True
    assert slg.ram_fits_machine(21.0, 23.0) is False
    assert slg.ram_fits_machine(21.0, 64.0) is True
    assert slg.ram_fits_machine(None, 15.0) is None


def test_unreadable_ram_never_refuses(monkeypatch):
    monkeypatch.setattr(slg, "total_ram_gb", lambda: None)
    assert slg.ram_fits_machine(500.0) is None
    assert slg.ram_capacity_refusal(500.0) is None


def test_the_refusal_names_system_ram_and_the_numbers():
    text = slg.ram_capacity_refusal(21.0, 15.0)
    assert "21.0 GB of system RAM (not VRAM)" in text
    assert "1.5 GB floor" in text
    assert "15.0 GB in all" in text
    assert "waiting will not help" in text
    assert "add system RAM" in text
    assert slg.ram_capacity_refusal(6.0, 15.0) is None


def test_under_wsl_the_advice_is_the_wslconfig_memory_line(monkeypatch):
    monkeypatch.setattr(slg, "running_under_wsl", lambda: True)
    text = slg.ram_capacity_refusal(21.0, 15.0)
    assert "Ubuntu sees 15.0 GB" in text
    assert "memory=" in text and "[wsl2]" in text and ".wslconfig" in text
    assert "wsl --shutdown" in text
    # The transient refusal carries the same advice.
    monkeypatch.setattr(slg, "total_ram_gb", lambda: 15.0)
    assert "memory=" in slg.ram_advice()


def test_the_admission_floor_is_unchanged():
    assert slg.effective_ram_hard_min_gb(15.0) == pytest.approx(1.5)
    assert slg.effective_ram_hard_min_gb(32.0) == pytest.approx(3.2)
    assert slg.effective_ram_hard_min_gb(128.0) == pytest.approx(6.0)


# --- the render-time stop ----------------------------------------------------

@pytest.mark.parametrize("total, stop", [
    (15.0, 2.25),
    (32.0, 4.8),
    (59.2, 8.0),       # the 60 GB box both figures were set on, as psutil reads it
    (128.0, 8.0),      # big boxes keep 8 GB
    (256.0, 0.06 * 256),
])
def test_render_stop_scales_like_the_admission_floor(total, stop):
    assert slg.render_ram_stop_gb(total) == pytest.approx(stop)


def test_render_stop_stays_above_the_admission_floor():
    for total in (8.0, 15.0, 24.0, 32.0, 48.0, 64.0):
        assert slg.render_ram_stop_gb(total) > slg.effective_ram_hard_min_gb(total)


def test_render_stop_override(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_MIN_FREE_RAM_GB", "5")
    assert slg.render_ram_stop_gb(128.0) == 5.0


def test_the_watchdog_uses_the_scaled_stop(monkeypatch):
    from backend.services import offline_image_generator as offline
    psutil = pytest.importorskip("psutil")
    gb = 1024 ** 3
    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=15 * gb, available=3 * gb))
    gen = offline.OfflineImageGenerator.__new__(offline.OfflineImageGenerator)
    cb = gen._ram_watchdog_callback()
    # 3 GB free on a 15 GB machine is above its 2.25 GB stop: the render goes on.
    assert cb(None, 0, None, {"k": 1}) == {"k": 1}
    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=15 * gb, available=1.5 * gb))
    with pytest.raises(offline.RamWatchdogAbort, match=r"floor 2\.2GB"):
        cb(None, 3, None, {})


# --- admission ---------------------------------------------------------------

def test_a_job_that_can_never_fit_is_refused_before_the_claim(monkeypatch):
    monkeypatch.setattr(slg, "total_ram_gb", lambda: 15.0)
    claimed = []
    from backend.services import job_arbiter
    monkeypatch.setattr(job_arbiter, "gpu_admission", lambda *a, **k: claimed.append("arbiter"))
    monkeypatch.setattr(grp, "reclaim_gpu", lambda *a, **k: claimed.append("reclaim"))
    from backend.services.job_operation_gate import get_gate

    with pytest.raises(SystemRamCapacityError) as caught:
        with grp.gpu_session(JobKind.VIDEO_RENDER, "ImageBatch_ram", vram_estimate_mb=11000,
                             ram_estimate_gb=21.0, require_fit=True, evict_ollama=True):
            pass  # pragma: no cover

    err = caught.value
    # Terminal for every retry loop, and still read as a system-load refusal.
    assert isinstance(err, GpuCapacityError) and isinstance(err, SystemLoadBusyError)
    assert str(err).startswith("Not enough system RAM — this job needs 21.0 GB")
    assert classify_render_exception(err) is RenderErrorKind.RAM_TOO_SMALL
    assert claimed == []
    assert get_gate().snapshot()["gpu_busy"] is False


def test_a_job_that_fits_is_not_refused_by_the_capacity_check(monkeypatch):
    monkeypatch.setattr(slg, "total_ram_gb", lambda: 15.0)
    grp._raise_if_ram_can_never_fit(6.0)  # must not raise


# --- the image batch's final text --------------------------------------------

def _batch(monkeypatch, tmp_path, refusal):
    from backend.services.batch_image_generator import (
        BatchGenerationStatus, BatchImageGenerator, BatchImageRequest, BatchPrompt,
    )
    monkeypatch.setattr(grp, "vram_probe_snapshot", lambda **_kw: {"free_mb": 10807})
    monkeypatch.setattr(grp, "compositor_vram_reserve_mb", lambda: 0)

    @contextmanager
    def refusing_session(*_a, **_kw):
        raise refusal
        yield  # pragma: no cover

    monkeypatch.setattr(grp, "gpu_session", refusing_session)
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    gen.progress_system = None
    gen.image_generator = SimpleNamespace(_device="cuda")
    gen.cancel_events = {}
    gen._kept_model_for_batch = lambda _req: None
    gen._batch_resource_estimates = lambda _req, _reuse: (11000, 21.0)
    gen._save_batch_metadata = lambda *_a, **_kw: None
    request = BatchImageRequest(
        batch_id="ImageBatch_ram",
        prompts=[BatchPrompt(id="p1", prompt="a lighthouse at dusk")],
        output_dir=str(tmp_path),
        save_metadata=False,
    )
    status = BatchGenerationStatus(batch_id="ImageBatch_ram", status="running",
                                   total_images=1, completed_images=0, failed_images=0)
    gen._run_batch_job(request, status, tmp_path)
    return status


def test_a_batch_that_can_never_fit_fails_at_once_naming_ram(monkeypatch, tmp_path):
    status = _batch(monkeypatch, tmp_path, SystemRamCapacityError(slg.ram_capacity_refusal(21.0, 15.0)))
    assert status.status == "error"
    assert status.error.startswith("Not enough system RAM — this job needs 21.0 GB")
    assert "VRAM after waiting" not in status.error
    assert status.error_kind == "ram_too_small"


def test_a_ram_wait_that_runs_out_names_ram_not_vram(monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_IMAGE_VRAM_WAIT_S", "0")
    detail = "system RAM too low (not VRAM): 8.3 GB available (reserved 0.0 GB), need 6.0 GB + 1.5 GB floor."
    status = _batch(monkeypatch, tmp_path, SystemLoadBusyError(detail))
    assert status.status == "error"
    assert status.error == f"The system did not free up after waiting 0s: {detail}"


# --- Auto among installed models ---------------------------------------------

def _gen(monkeypatch, *, downloaded, total_ram):
    from backend.services import media_model_registry as mmr
    from backend.services import offline_image_generator as offline
    gen = offline.OfflineImageGenerator.__new__(offline.OfflineImageGenerator)
    gen.available_models = {
        "zimage-turbo": "Tongyi-MAI/Z-Image-Turbo",
        "comfyui": "comfy:comfyui",
        "krea2-turbo": "krea/Krea-2-Turbo",
        "sd-xl": "stabilityai/stable-diffusion-xl-base-1.0",
        "realistic-vision": "SG161222/Realistic_Vision_V5.1_noVAE",
    }
    gen.comfy_only_models = {"comfyui"}
    gen.user_entries, gen.user_files, gen.family_overrides = {}, {}, {}
    gen._device = "cuda"
    on_disk = {gen.available_models[k] for k in downloaded}
    monkeypatch.setattr(gen, "_is_model_downloaded", lambda model_id: model_id in on_disk)
    monkeypatch.setattr(gen, "_cuda_total_vram_gb", lambda: 12.0)
    monkeypatch.setattr(gen, "detect_content_type", lambda prompt: {})
    monkeypatch.setattr(mmr, "configured_stills_default", lambda: None)
    monkeypatch.setattr(slg, "total_ram_gb", lambda: total_ram)
    return gen


def test_auto_skips_an_installed_model_this_machine_cannot_hold(monkeypatch):
    gen = _gen(monkeypatch, downloaded={"zimage-turbo", "realistic-vision"}, total_ram=15.0)
    assert gen.model_ram_need_gb("zimage-turbo") == pytest.approx(21.0)
    assert gen.model_fits_ram("zimage-turbo", 15.0) is False
    assert gen._auto_select_model("a portrait of a woman") == "realistic-vision"


def test_auto_is_unchanged_where_the_ram_is_there(monkeypatch):
    gen = _gen(monkeypatch, downloaded={"zimage-turbo", "realistic-vision"}, total_ram=64.0)
    assert gen._auto_select_model("a portrait of a woman") == "zimage-turbo"


def test_auto_keeps_the_first_installed_when_nothing_fits(monkeypatch):
    # The refusal then says why, instead of Auto reporting nothing installed.
    gen = _gen(monkeypatch, downloaded={"zimage-turbo", "krea2-turbo"}, total_ram=15.0)
    assert gen._auto_select_model("a lighthouse") == "zimage-turbo"


def test_the_router_row_has_no_ram_figure(monkeypatch):
    gen = _gen(monkeypatch, downloaded=set(), total_ram=15.0)
    assert gen.model_ram_need_gb("comfyui") is None
    assert gen.model_fits_ram("comfyui", 15.0) is True
