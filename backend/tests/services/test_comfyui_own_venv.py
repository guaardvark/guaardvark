"""ComfyUI's own environment is opt-in: the Update action installs into the
Python start.sh runs ComfyUI from, which is the plugin's venv only when
GUAARDVARK_COMFYUI_OWN_VENV=1 and the venv is built."""
import pytest

try:
    from backend.services import comfyui_update_service as cus
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def _plugin(tmp_path, built: bool):
    if built:
        py = tmp_path / "venv" / "bin" / "python"
        py.parent.mkdir(parents=True)
        py.write_text("")
    return tmp_path


def test_the_backend_venv_is_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_COMFYUI_OWN_VENV", raising=False)
    assert cus.comfyui_python(_plugin(tmp_path, built=True)) == cus.DEFAULT_PYTHON


def test_the_switch_picks_the_plugin_venv_when_it_is_built(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_OWN_VENV", "1")
    root = _plugin(tmp_path, built=True)
    assert cus.comfyui_python(root) == root / "venv" / "bin" / "python"


def test_the_switch_without_a_built_venv_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_OWN_VENV", "1")
    assert cus.comfyui_python(_plugin(tmp_path, built=False)) == cus.DEFAULT_PYTHON
