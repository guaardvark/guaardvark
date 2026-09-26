"""``OfflineImageGenerator._comfyui_assets_present`` — the ComfyUI availability probe.

This drives whether the ``comfyui`` catalog row reads as installed (and whether
``get_available_models`` can reach the cached engine list at all). The probe is a
thin wrapper around ``ComfyUIImageGenerator.comfyui_installed_engines`` plus the
Z-Image opt-in filter, and it swallows every exception — so a mistake inside it
(other than a missing name) reads as "ComfyUI absent" rather than raising.

Regression pinned here: a missing ``gen = ComfyUIImageGenerator()`` assignment
raised ``NameError``, was swallowed by ``except Exception``, and made this return
``False`` unconditionally — no test called it directly, so CI stayed green.
"""
import pytest

try:
    from backend.services import comfyui_image_generator as cig
    from backend.services.offline_image_generator import OfflineImageGenerator
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def _stub_engines(monkeypatch, engines):
    monkeypatch.setattr(
        cig.ComfyUIImageGenerator, "comfyui_installed_engines", lambda self: list(engines)
    )


def test_zimage_dropped_when_flag_off_but_flux_still_counts(monkeypatch):
    # With the opt-in off the generic selector cannot use Z-Image, so only FLUX
    # engines count — but a machine with FLUX installed is still "available".
    monkeypatch.delenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", raising=False)
    _stub_engines(monkeypatch, ["zimage", "flux-dev"])

    assert OfflineImageGenerator._comfyui_assets_present() is True


def test_zimage_alone_is_absent_when_flag_off(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", raising=False)
    _stub_engines(monkeypatch, ["zimage"])

    assert OfflineImageGenerator._comfyui_assets_present() is False


def test_zimage_counts_when_opted_in(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", "1")
    _stub_engines(monkeypatch, ["zimage"])

    assert OfflineImageGenerator._comfyui_assets_present() is True


def test_no_engines_is_absent_regardless_of_flag(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", "1")
    _stub_engines(monkeypatch, [])

    assert OfflineImageGenerator._comfyui_assets_present() is False


def test_engine_probe_failure_reads_as_absent(monkeypatch):
    # An unreachable server (or any other failure) must read as absent, never raise.
    monkeypatch.setenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", "1")

    def boom(self):
        raise RuntimeError("ComfyUI down")

    monkeypatch.setattr(cig.ComfyUIImageGenerator, "comfyui_installed_engines", boom)

    assert OfflineImageGenerator._comfyui_assets_present() is False
