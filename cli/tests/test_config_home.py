"""CLI config lives in ~/.guaardvark/cli.json with ~/.llx fallback."""

import json

from llx import config as cfg


def test_load_prefers_new_path(tmp_path, monkeypatch):
    new_dir = tmp_path / ".guaardvark"
    new_dir.mkdir()
    new_file = new_dir / "cli.json"
    new_file.write_text(json.dumps({"theme": "musk", "server": "http://x"}))
    monkeypatch.setattr(cfg, "GUAARDVARK_DIR", new_dir)
    monkeypatch.setattr(cfg, "CONFIG_DIR", new_dir)
    monkeypatch.setattr(cfg, "CONFIG_FILE", new_file)
    monkeypatch.setattr(cfg, "LEGACY_CONFIG_FILE", tmp_path / ".llx" / "config.json")
    loaded = cfg.load_config()
    assert loaded["theme"] == "musk"
    assert loaded["server"] == "http://x"


def test_load_falls_back_to_legacy(tmp_path, monkeypatch):
    legacy_dir = tmp_path / ".llx"
    legacy_dir.mkdir()
    legacy = legacy_dir / "config.json"
    legacy.write_text(json.dumps({"theme": "vader"}))
    new_dir = tmp_path / ".guaardvark"
    monkeypatch.setattr(cfg, "CONFIG_FILE", new_dir / "cli.json")
    monkeypatch.setattr(cfg, "LEGACY_CONFIG_FILE", legacy)
    loaded = cfg.load_config()
    assert loaded["theme"] == "vader"


def test_save_writes_new_path_only(tmp_path, monkeypatch):
    new_dir = tmp_path / ".guaardvark"
    new_file = new_dir / "cli.json"
    legacy = tmp_path / ".llx" / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_DIR", new_dir)
    monkeypatch.setattr(cfg, "CONFIG_FILE", new_file)
    monkeypatch.setattr(cfg, "LEGACY_CONFIG_FILE", legacy)
    cfg.save_config({"theme": "teal"})
    assert new_file.is_file()
    assert not legacy.exists()
    assert json.loads(new_file.read_text())["theme"] == "teal"


def test_get_frontend_url_runtime(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"frontend_port": 5199}))
    monkeypatch.setattr(cfg, "RUNTIME_FILE", runtime)
    monkeypatch.delenv("GUAARDVARK_FRONTEND", raising=False)
    monkeypatch.delenv("VITE_PORT", raising=False)
    assert cfg.get_frontend_url() == "http://localhost:5199"


def test_get_frontend_url_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_FRONTEND", "http://localhost:5999/")
    monkeypatch.setattr(cfg, "RUNTIME_FILE", tmp_path / "missing.json")
    assert cfg.get_frontend_url() == "http://localhost:5999"
