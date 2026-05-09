import json

from scripts.dep_reconciler.registry import (
    classify_plugin_venv_mode,
    enabled_plugin_ids,
)


def test_classify_isolated_when_setup_venv_sh_exists(tmp_path):
    plugin = tmp_path / "lora_trainer"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "scripts" / "setup_venv.sh").write_text("#!/bin/bash\n")
    assert classify_plugin_venv_mode(plugin) == "isolated"


def test_classify_isolated_when_venv_dir_exists(tmp_path):
    plugin = tmp_path / "audio_foundry"
    (plugin / "venv-music").mkdir(parents=True)
    assert classify_plugin_venv_mode(plugin) == "isolated"


def test_classify_shared_when_no_isolated_indicators(tmp_path):
    plugin = tmp_path / "discord"
    plugin.mkdir()
    (plugin / "requirements.txt").write_text("aiohttp\n")
    assert classify_plugin_venv_mode(plugin) == "shared"


def test_enabled_plugins_reads_user_enabled_flag(tmp_path):
    state_file = tmp_path / "plugin_state.json"
    state_file.write_text(json.dumps({
        "discord": {"user_enabled": True},
        "swarm":   {"user_enabled": False},
        "vision_pipeline": {"user_enabled": True},
    }))
    ids = enabled_plugin_ids(state_file)
    assert sorted(ids) == ["discord", "vision_pipeline"]


def test_enabled_plugins_missing_file_returns_empty(tmp_path):
    assert enabled_plugin_ids(tmp_path / "nope.json") == []


def test_enabled_plugins_corrupt_file_returns_empty(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json}")
    assert enabled_plugin_ids(bad) == []
