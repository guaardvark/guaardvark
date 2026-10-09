"""Which API key the CLI sends: env vars, then its config, then, for a server
on this machine, the local install's .env (where Settings → Access and
start-docker.sh keep it)."""

import json

import pytest

from llx import config as cfg
from llx import launch_config


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv(cfg.ENV_API_KEY, raising=False)
    monkeypatch.delenv(cfg.ENV_API_KEY_ALT, raising=False)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "home" / "cli.json")
    monkeypatch.setattr(cfg, "LEGACY_CONFIG_FILE", tmp_path / "home" / "legacy.json")
    root = tmp_path / "guaardvark"
    root.mkdir()
    (root / ".env").write_text("FLASK_PORT=5000\nGUAARDVARK_API_KEY=from-install\n")
    monkeypatch.setattr(launch_config, "resolve_guaardvark_root", lambda: root)
    return tmp_path


def test_a_local_server_uses_the_install_key(isolated):
    assert cfg.get_api_key("http://localhost:5000") == "from-install"
    assert cfg.get_api_key("http://127.0.0.1:5055") == "from-install"


def test_another_server_never_gets_the_install_key(isolated):
    assert cfg.get_api_key("http://192.168.1.20:5000") is None
    assert cfg.get_api_key() is None


def test_the_environment_and_the_config_come_first(isolated, monkeypatch):
    cfg.CONFIG_FILE.parent.mkdir(parents=True)
    cfg.CONFIG_FILE.write_text(json.dumps({"api_key": "from-config"}))
    assert cfg.get_api_key("http://localhost:5000") == "from-config"
    monkeypatch.setenv(cfg.ENV_API_KEY, "from-env")
    assert cfg.get_api_key("http://localhost:5000") == "from-env"


def test_no_install_found_means_no_key(isolated, monkeypatch):
    monkeypatch.setattr(launch_config, "resolve_guaardvark_root", lambda: None)
    assert cfg.get_api_key("http://localhost:5000") is None
