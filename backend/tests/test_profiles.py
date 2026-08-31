"""Profiles: one switch sets the product shape, an explicit value always wins,
and `workstation` changes nothing at all."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import profiles as P


@pytest.fixture
def env():
    return {"PATH": "/usr/bin"}


def test_workstation_is_a_no_op(env):
    p = P.load_profile("workstation", environ=env)
    assert p.is_default and p.fallback_reason is None
    assert P.apply_env(p, env) == []
    assert env == {"PATH": "/usr/bin"}
    assert P.shell_exports(p, env) == ["export GUAARDVARK_PROFILE_ACTIVE='workstation'"]


def test_unset_env_means_workstation(env):
    assert P.requested_name(env) == "workstation"
    assert P.load_profile(environ=env).name == "workstation"


def test_creator_fills_only_unset_keys(env):
    env["GUAARDVARK_AGENT_BRAIN"] = "true"  # the operator's explicit choice
    p = P.load_profile("creator", environ=env)
    applied = P.apply_env(p, env)
    assert "GUAARDVARK_AGENT_BRAIN" not in applied
    assert env["GUAARDVARK_AGENT_BRAIN"] == "true"
    assert env["GUAARDVARK_MCP_ENABLED"] == "false"
    assert env[P.PLUGIN_DEFAULTS_ENV] == "audio_foundry=true,comfyui=true,lora_trainer=true,swarm=true,upscaling=true,video_editor=true"
    assert env["GUAARDVARK_PROFILE_VOICE_CHECK"] == "0"
    # Second application is idempotent.
    assert P.apply_env(p, env) == []


def test_creator_hides_but_never_removes(env):
    p = P.load_profile("creator", environ=env)
    assert "/outreach" in p.hidden_routes and "/film-crew" not in p.hidden_routes
    assert p.landing_route == "/images"
    public = p.public_dict()
    assert set(public) >= {"name", "label", "hidden_routes", "landing_route", "chat_surfaces", "brand", "source"}
    assert "env" not in public and "path" not in public


def test_unknown_profile_falls_back_and_says_why(env):
    p = P.load_profile("does-not-exist", environ=env)
    assert p.name == "workstation"
    assert "not found" in p.fallback_reason
    assert p.public_dict()["fallback_reason"]
    assert P.apply_env(p, env) == []


def test_invalid_name_is_rejected_not_executed(env):
    p = P.load_profile("../etc/passwd", environ=env)
    assert p.name == "workstation" and "not valid" in p.fallback_reason


def test_extension_profile_is_found_under_its_folder_name(tmp_path, env):
    ext = tmp_path / "extensions" / "acme"
    ext.mkdir(parents=True)
    (ext / "profile.json").write_text(json.dumps({
        "name": "acme", "label": "Acme", "env": {"GUAARDVARK_MCP_ENABLED": False},
        "plugins": {"comfyui": "yes"}, "nav": {"hidden": ["/outreach"]},
        "landing_route": "/acme", "brand": {"app_name": "Acme Brain"},
        "default_models": {"chat": "gemma4:e2b"},
    }))
    # underscore folders are templates and must not register
    (tmp_path / "extensions" / "_template").mkdir()
    (tmp_path / "extensions" / "_template" / "profile.json").write_text("{}")

    found = P.available_profiles(root=tmp_path)
    assert found["acme"][0] == "extension" and "_template" not in found
    p = P.load_profile("acme", root=tmp_path, environ=env)
    assert p.source == "extension" and p.brand["app_name"] == "Acme Brain"
    assert p.env == {"GUAARDVARK_MCP_ENABLED": "false"}     # booleans become flag strings
    assert p.plugins == {"comfyui": True}
    P.apply_env(p, env)
    assert env["GUAARDVARK_DEFAULT_LLM"] == "gemma4:e2b"


def test_unknown_keys_warn_but_load(tmp_path, env):
    ext = tmp_path / "extensions" / "acme"
    ext.mkdir(parents=True)
    (ext / "profile.json").write_text(json.dumps({
        "name": "acme", "bogus": 1, "startup": {"voice_check": False, "made_up": True},
        "nav": {"hidden": [], "add": ["/x"]}, "env": {"lowercase": "x"},
    }))
    p = P.load_profile("acme", root=tmp_path, environ=env)
    assert p.fallback_reason is None
    assert p.startup == {"voice_check": False}
    assert p.env == {}
    joined = " ".join(p.warnings)
    assert "bogus" in joined and "made_up" in joined and "add" in joined and "lowercase" in joined


def test_unreadable_profile_falls_back(tmp_path, env):
    ext = tmp_path / "extensions" / "acme"
    ext.mkdir(parents=True)
    (ext / "profile.json").write_text("{not json")
    p = P.load_profile("acme", root=tmp_path, environ=env)
    assert p.name == "workstation" and "unreadable" in p.fallback_reason


def test_shell_exports_are_quoted_and_skip_present_keys(env):
    env["GUAARDVARK_MCP_ENABLED"] = "true"
    p = P.load_profile("creator", environ=env)
    lines = P.shell_exports(p, env)
    assert all(line.startswith("export ") and "='" in line for line in lines)
    assert not any("GUAARDVARK_MCP_ENABLED" in line for line in lines)
    assert "export GUAARDVARK_AGENT_BRAIN='false'" in lines


def test_plugin_defaults_round_trip():
    assert P.parse_plugin_defaults("a=true,b=false, c = yes ,junk") == {"a": True, "b": False, "c": True}
    assert P.parse_plugin_defaults(None) == {}


def test_plugin_manifest_honours_profile_default(tmp_path, monkeypatch):
    from backend.plugins.plugin_base import PluginMetadata
    plugin_dir = tmp_path / "comfyui"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(json.dumps({"id": "comfyui", "name": "ComfyUI", "config": {"default_enabled": False}}))

    monkeypatch.delenv(P.PLUGIN_DEFAULTS_ENV, raising=False)
    assert PluginMetadata.from_json_file(plugin_dir / "plugin.json").config.enabled is False

    monkeypatch.setenv(P.PLUGIN_DEFAULTS_ENV, "comfyui=true,other=false")
    assert PluginMetadata.from_json_file(plugin_dir / "plugin.json").config.enabled is True

    # A plugin the profile does not mention keeps its manifest default.
    other = tmp_path / "upscaling"; other.mkdir()
    (other / "plugin.json").write_text(json.dumps({"id": "upscaling", "config": {"default_enabled": False}}))
    assert PluginMetadata.from_json_file(other / "plugin.json").config.enabled is False


def test_importing_config_with_no_profile_leaves_environ_alone(monkeypatch):
    monkeypatch.delenv(P.PROFILE_ENV, raising=False)
    before = dict(os.environ)
    P.apply_env(P.active_profile(refresh=True))
    after = dict(os.environ)
    assert {k: v for k, v in after.items() if before.get(k) != v} == {}


def test_set_configured_name_replaces_line_and_keeps_the_rest(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FLASK_PORT=5000\nGUAARDVARK_PROFILE=workstation\nSECRET_KEY=abc\n")
    P.set_configured_name("creator", root=tmp_path)
    assert env.read_text() == "FLASK_PORT=5000\nGUAARDVARK_PROFILE=creator\nSECRET_KEY=abc\n"
    assert P.configured_name(root=tmp_path) == "creator"
    assert not env.with_name(".env.tmp").exists()


def test_set_configured_name_appends_when_absent_even_without_trailing_newline(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FLASK_PORT=5000")
    P.set_configured_name("creator", root=tmp_path)
    assert env.read_text() == "FLASK_PORT=5000\nGUAARDVARK_PROFILE=creator\n"


def test_set_configured_name_creates_env_when_missing(tmp_path):
    P.set_configured_name("workstation", root=tmp_path)
    assert (tmp_path / ".env").read_text() == "GUAARDVARK_PROFILE=workstation\n"


def test_set_configured_name_rejects_unknown_and_invalid(tmp_path):
    with pytest.raises(ValueError):
        P.set_configured_name("nope", root=tmp_path)
    with pytest.raises(ValueError):
        P.set_configured_name("../x", root=tmp_path)
    assert not (tmp_path / ".env").exists()


def test_configured_name_reads_quoted_values(tmp_path):
    (tmp_path / ".env").write_text('GUAARDVARK_PROFILE="creator"\n')
    assert P.configured_name(root=tmp_path) == "creator"
    assert P.configured_name(root=tmp_path / "missing") is None
