"""The coordinator's Ollama check under Docker, where Ollama is another container."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from backend.services import gpu_resource_coordinator as grc


@pytest.fixture
def coordinator():
    # Only the Ollama check is exercised; no lock file is read or written.
    return grc.GPUResourceCoordinator.__new__(grc.GPUResourceCoordinator)


def test_docker_asks_ollama_over_http(coordinator, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_DOCKER", "1")
    with patch.object(grc.subprocess, "run") as run, \
         patch.object(grc.requests, "get", return_value=MagicMock(ok=True)) as get:
        assert coordinator._is_ollama_running() is True
    run.assert_not_called()
    assert get.call_args[0][0].endswith("/api/version")


def test_docker_reports_an_unreachable_ollama_quietly(coordinator, monkeypatch, caplog):
    monkeypatch.setenv("GUAARDVARK_DOCKER", "1")
    with patch.object(grc.subprocess, "run") as run, \
         patch.object(grc.requests, "get", side_effect=requests.ConnectionError("refused")), \
         caplog.at_level("WARNING", logger=grc.logger.name):
        assert coordinator._is_ollama_running() is False
    run.assert_not_called()
    assert not caplog.records


def test_native_still_asks_systemctl(coordinator, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_DOCKER", raising=False)
    with patch.object(grc.subprocess, "run", return_value=MagicMock(returncode=0)) as run, \
         patch.object(grc.requests, "get") as get:
        assert coordinator._is_ollama_running() is True
    assert run.call_args[0][0][0] == "systemctl"
    get.assert_not_called()


def test_native_external_ollama_is_checked_as_before(coordinator, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_DOCKER", raising=False)
    monkeypatch.setenv("GUAARDVARK_OLLAMA_EXTERNAL", "1")
    with patch.object(grc.subprocess, "run", return_value=MagicMock(returncode=0)) as run, \
         patch.object(grc.requests, "get") as get:
        assert coordinator._is_ollama_running() is True
    run.assert_called()
    get.assert_not_called()
