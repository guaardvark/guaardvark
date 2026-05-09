import logging

import pytest

from scripts.dep_reconciler.detectors.torch_venv import TorchVenvDetector


@pytest.fixture
def fake_repo(tmp_path):
    # lora_trainer: has setup_venv.sh, no venv yet
    (tmp_path / "plugins" / "lora_trainer" / "scripts").mkdir(parents=True)
    (tmp_path / "plugins" / "lora_trainer" / "scripts" / "setup_venv.sh").write_text("#!/bin/bash\n")
    # audio_foundry: has setup_venv.sh AND venv-music exists
    (tmp_path / "plugins" / "audio_foundry" / "scripts").mkdir(parents=True)
    (tmp_path / "plugins" / "audio_foundry" / "scripts" / "setup_venv.sh").write_text("#!/bin/bash\n")
    (tmp_path / "plugins" / "audio_foundry" / "venv-music" / "bin").mkdir(parents=True)
    (tmp_path / "plugins" / "audio_foundry" / "venv-music" / "bin" / "python").write_text("#!/bin/bash\n")
    return tmp_path


def test_id(fake_repo):
    assert TorchVenvDetector(fake_repo).id == "torch_venv_detector"


def test_run_returns_warnings_for_missing_venvs(fake_repo, caplog):
    caplog.set_level(logging.WARNING)
    d = TorchVenvDetector(fake_repo)
    warnings = d.detect()
    assert any("lora_trainer" in w for w in warnings)
    # audio_foundry has its venv-music, so it should NOT warn
    assert not any("audio_foundry" in w for w in warnings)


def test_run_no_warnings_when_no_isolated_plugins(tmp_path):
    (tmp_path / "plugins").mkdir()
    d = TorchVenvDetector(tmp_path)
    assert d.detect() == []
