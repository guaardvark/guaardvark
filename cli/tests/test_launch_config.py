"""Tests for launch config read/write."""
import json
import os
import pytest
from pathlib import Path


@pytest.fixture
def config_dir(tmp_path):
    """Use a temporary directory instead of ~/.guaardvark."""
    config_path = tmp_path / ".guaardvark"
    config_path.mkdir()
    return config_path


@pytest.fixture
def patch_config_dir(monkeypatch, config_dir):
    """Patch the config directory to use tmp_path."""
    monkeypatch.setattr("llx.launch_config._config_dir", lambda: config_dir)
    return config_dir


class TestLaunchConfig:
    def test_default_config_values(self, patch_config_dir):
        from llx.launch_config import load_launch_config
        cfg = load_launch_config()
        assert cfg["onboarded"] is False
        assert cfg["mode"] == "lite"
        assert cfg["model"] is None
        assert cfg["ollama_base_url"] == "http://127.0.0.1:11434"
        assert cfg["server_url"] == "http://localhost:5000"
        assert cfg["auto_start_services"] is True
        assert cfg["guaardvark_root"] is None

    def test_save_and_load_roundtrip(self, patch_config_dir, config_dir):
        from llx.launch_config import load_launch_config, save_launch_config
        cfg = load_launch_config()
        cfg["onboarded"] = True
        cfg["model"] = "llama3.3"
        save_launch_config(cfg)

        loaded = load_launch_config()
        assert loaded["onboarded"] is True
        assert loaded["model"] == "llama3.3"
        assert loaded["mode"] == "lite"

    def test_config_file_created_on_save(self, patch_config_dir, config_dir):
        from llx.launch_config import save_launch_config
        save_launch_config({"onboarded": True, "model": "test"})
        assert (config_dir / "config.json").exists()

    def test_is_first_launch_true_when_no_config(self, patch_config_dir):
        from llx.launch_config import is_first_launch
        assert is_first_launch() is True

    def test_is_first_launch_false_when_onboarded(self, patch_config_dir, config_dir):
        from llx.launch_config import save_launch_config, is_first_launch
        save_launch_config({"onboarded": True})
        assert is_first_launch() is False

    def test_resolve_ollama_url_from_env(self, monkeypatch, patch_config_dir):
        from llx.launch_config import resolve_ollama_url
        monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.100:11434")
        assert resolve_ollama_url() == "http://192.168.1.100:11434"

    def test_resolve_ollama_url_from_config(self, monkeypatch, patch_config_dir, config_dir):
        from llx.launch_config import save_launch_config, resolve_ollama_url
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        save_launch_config({"ollama_base_url": "http://10.0.0.5:11434"})
        assert resolve_ollama_url() == "http://10.0.0.5:11434"

    def test_resolve_ollama_url_default(self, monkeypatch, patch_config_dir):
        from llx.launch_config import resolve_ollama_url
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        assert resolve_ollama_url() == "http://127.0.0.1:11434"

    def test_save_preserves_unknown_keys(self, patch_config_dir, config_dir):
        """Ollama's Go Editor may write keys we don't know about."""
        from llx.launch_config import load_launch_config, save_launch_config
        raw = {"onboarded": True, "custom_field": "keep_me"}
        (config_dir / "config.json").write_text(json.dumps(raw))
        cfg = load_launch_config()
        cfg["model"] = "test"
        save_launch_config(cfg)
        reloaded = json.loads((config_dir / "config.json").read_text())
        assert reloaded["custom_field"] == "keep_me"
