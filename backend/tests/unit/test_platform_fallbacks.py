"""Fixes from the macOS install thread (#41): ComfyUI's effective port, font
discovery, the preflight message, and the screen agent's message on macOS."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def test_comfyui_url_explicit_env_wins(monkeypatch):
    from backend.utils import comfyui_url

    monkeypatch.setenv("GUAARDVARK_COMFYUI_URL", "http://127.0.0.1:8000/")
    assert comfyui_url.get_comfyui_url() == "http://127.0.0.1:8000"


def test_comfyui_url_follows_the_plugin_port(monkeypatch):
    from backend.utils import comfyui_url

    monkeypatch.delenv("GUAARDVARK_COMFYUI_URL", raising=False)

    class FakeManager:
        def get_plugin_info(self, plugin_id):
            assert plugin_id == "comfyui"
            return {"port": 8000}

    monkeypatch.setattr("backend.plugins.plugin_manager.get_plugin_manager", lambda: FakeManager())
    assert comfyui_url.get_comfyui_url() == "http://127.0.0.1:8000"


def test_comfyui_url_falls_back_to_the_manifest_default(monkeypatch):
    from backend.utils import comfyui_url

    monkeypatch.delenv("GUAARDVARK_COMFYUI_URL", raising=False)

    def boom():
        raise RuntimeError("no plugin manager here")

    monkeypatch.setattr("backend.plugins.plugin_manager.get_plugin_manager", boom)
    assert comfyui_url.get_comfyui_url() == "http://127.0.0.1:8188"


def test_infographic_generator_uses_the_effective_port(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_URL", "http://127.0.0.1:8000")
    from backend.services.infographic_generator import InfographicGenerator

    assert InfographicGenerator().comfy_url == "http://127.0.0.1:8000"
    assert InfographicGenerator("http://10.0.0.5:8188/").comfy_url == "http://10.0.0.5:8188"


def test_font_override_and_candidates(monkeypatch, tmp_path):
    from backend.services import video_text_overlay as vto

    font = tmp_path / "Bold.ttf"
    font.write_bytes(b"\x00")
    monkeypatch.setenv("GUAARDVARK_OVERLAY_FONT", str(font))
    assert vto.resolve_font_path() == str(font)

    monkeypatch.setenv("GUAARDVARK_OVERLAY_FONT", str(tmp_path / "missing.ttf"))
    with pytest.raises(vto.VideoOverlayError):
        vto.resolve_font_path()

    monkeypatch.delenv("GUAARDVARK_OVERLAY_FONT")
    monkeypatch.setattr(vto, "_FONT_CANDIDATES", (str(tmp_path / "nope.ttf"), str(font)))
    assert vto.resolve_font_path() == str(font)


def test_preflight_names_the_failing_reconciler(monkeypatch, tmp_path, capsys):
    sys.path.insert(0, str(Path("scripts").resolve()))
    import preflight_check as pc

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / ".dep_reconcile_failed").write_text(
        "dep_reconciler failed at 2026-08-24T00:00:00Z\n"
        "  - plugin_bundle: pip failed for plugin 'audio_foundry'\n"
        "Full log: logs/dep_reconciler/20260824.log\n"
    )
    monkeypatch.setattr(pc, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(pc, "errors", [])
    assert pc.check_reconciler_sentinel() is False
    assert len(pc.errors) == 1
    msg = pc.errors[0]
    assert "plugin_bundle: pip failed for plugin 'audio_foundry'" in msg
    assert "Full log: logs/dep_reconciler/20260824.log" in msg


def test_screen_agent_explains_itself_on_macos(monkeypatch):
    from backend.tools import agent_control_tools as act

    monkeypatch.setattr("platform.system", lambda: "Darwin")
    with pytest.raises(RuntimeError) as exc:
        act._ensure_agent_display()
    assert "macOS" in str(exc.value) and "Xvfb" in str(exc.value)
    assert "start_agent_display.sh" not in str(exc.value)
