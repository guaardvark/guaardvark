"""Publish gates, secret isolation and job-registry wiring."""

import importlib

import pytest

from backend.services.connections import gates


@pytest.fixture
def settings(monkeypatch):
    """In-memory stand-in for the Setting table."""
    store = {}
    monkeypatch.setattr(gates, "_setting", lambda key, default: store.get(key, default))
    return store


# --- gates -------------------------------------------------------------------
def test_publishing_is_enabled_by_default(settings):
    assert gates.publish_enabled() is True


def test_publishing_can_be_disabled(settings):
    settings[gates.PUBLISH_ENABLED_KEY] = "false"
    assert gates.publish_enabled() is False


def test_supervision_is_off_by_default_for_the_ui(settings):
    assert gates.publish_supervised() is False
    assert gates.requires_approval("ui") is False


@pytest.mark.parametrize("source", ["chat", "mcp", "schedule"])
def test_agent_initiated_publishes_always_require_approval(settings, source):
    """An agent must never publish without a human click."""
    assert gates.publish_supervised() is False
    assert gates.requires_approval(source) is True


def test_supervised_mode_gates_the_ui_too(settings):
    settings[gates.PUBLISH_SUPERVISED_KEY] = "true"
    assert gates.requires_approval("ui") is True


# --- source attribution ------------------------------------------------------
# requested_by arrives in an unauthenticated request body, so it is a claim.
# Claiming an agent source only raises supervision; anything else must fail safe.
@pytest.mark.parametrize("claimed", ["ui", "chat", "mcp", "schedule"])
def test_known_sources_are_preserved(claimed):
    assert gates.normalize_source(claimed) == claimed


@pytest.mark.parametrize("claimed", [None, "", "   ", "api", "cron", "UI-ish", "🙂"])
def test_unrecognised_sources_become_unknown(claimed):
    assert gates.normalize_source(claimed) == "unknown"


def test_source_matching_ignores_case_and_padding():
    assert gates.normalize_source("  MCP  ") == "mcp"


def test_an_unattributed_publish_is_supervised(settings):
    """A caller that omits its source must not land on the unsupervised branch."""
    assert gates.publish_supervised() is False
    assert gates.requires_approval(gates.normalize_source(None)) is True


def test_disabled_publishing_blocks_the_gate(settings):
    settings[gates.PUBLISH_ENABLED_KEY] = "false"
    allowed, reason = gates.check_can_publish("bluesky")
    assert allowed is False
    assert "disabled" in reason.lower()


def test_cadence_backend_failure_fails_closed(settings, monkeypatch):
    """A missing rate-limit backend must refuse the post, not wave it through."""
    import backend.services.social_outreach.kill_switch as ks

    monkeypatch.setattr(
        ks, "cadence_allows_post", lambda p: (_ for _ in ()).throw(RuntimeError("redis down"))
    )
    allowed, reason = gates.check_can_publish("bluesky")
    assert allowed is False
    assert "refusing" in reason.lower()


def test_cadence_block_is_surfaced(settings, monkeypatch):
    import backend.services.social_outreach.kill_switch as ks

    monkeypatch.setattr(ks, "cadence_allows_post", lambda p: (False, "too soon"))
    allowed, reason = gates.check_can_publish("bluesky")
    assert allowed is False
    assert reason == "too soon"


def test_cadence_pass_allows_publishing(settings, monkeypatch):
    import backend.services.social_outreach.kill_switch as ks

    monkeypatch.setattr(ks, "cadence_allows_post", lambda p: (True, None))
    assert gates.check_can_publish("bluesky") == (True, None)


# --- secret isolation --------------------------------------------------------
def test_connection_secrets_never_join_the_cluster_sync_allowlist():
    """PORTABLE_ENV_KEYS syncs across nodes; per-operator tokens must not."""
    from backend.services.interconnector_file_sync_service import PORTABLE_ENV_KEYS

    from backend.services.connections import registry

    for spec in registry.list_specs():
        for field in spec.credential_fields:
            assert field.name.upper() not in PORTABLE_ENV_KEYS


def test_credential_file_lives_outside_the_repo(tmp_path, monkeypatch):
    """A release archive walks the repo; credentials must not be reachable there."""
    from pathlib import Path

    from backend.utils import credential_store

    monkeypatch.delenv("GUAARDVARK_CONFIG_DIR", raising=False)
    importlib.reload(credential_store)
    path = credential_store.credentials_path().resolve()
    # Derived, not a literal: a hardcoded clone name would make this pass
    # everywhere except the one machine it was written on.
    repo_root = Path(__file__).resolve().parents[3]
    assert repo_root not in path.parents, f"credentials inside the repo: {path}"
    assert str(path).endswith(".config/guaardvark/credentials.json")


def test_redact_strips_stored_secrets_from_error_text(tmp_path, monkeypatch):
    from backend.services.connections import service
    from backend.utils import credential_store

    monkeypatch.setenv("GUAARDVARK_CONFIG_DIR", str(tmp_path))
    importlib.reload(credential_store)
    monkeypatch.setattr(service, "credential_store", credential_store)

    credential_store.set_secret("social:x:default", {"token": "hunter2-hunter2"})
    cleaned = service.redact("Provider said: bad token hunter2-hunter2 rejected")
    assert "hunter2-hunter2" not in cleaned
    assert "••••" in cleaned


# --- job wiring --------------------------------------------------------------
def test_publish_is_a_registered_job_kind():
    from backend.services.job_cancel import CANCEL_DISPATCH
    from backend.services.job_registry import REGISTRY
    from backend.services.job_types import JobKind

    assert JobKind.PUBLISH in REGISTRY
    assert JobKind.PUBLISH in CANCEL_DISPATCH


def test_publish_has_a_collector():
    from backend.api.unified_jobs_resource_api import _COLLECTORS
    from backend.services.job_types import JobKind

    assert JobKind.PUBLISH in _COLLECTORS


def test_publish_tasks_are_excluded_from_the_generic_task_collector():
    """Otherwise every publish double-lists under both 'task' and 'publish'."""
    import inspect

    from backend.api import unified_jobs_resource_api as api

    source = inspect.getsource(api._collect_tasks)
    assert "connection_publish" in source


def test_publish_status_maps_like_a_task():
    from backend.services.job_types import JobKind, JobStatus, map_status

    assert map_status(JobKind.PUBLISH, "running") == JobStatus.RUNNING
    assert map_status(JobKind.PUBLISH, "completed") == JobStatus.COMPLETED
    assert map_status(JobKind.PUBLISH, "failed") == JobStatus.FAILED


# --- publish lifecycle -------------------------------------------------------
class _Record:
    """Minimal stand-in for PublishRecord; only status is exercised here."""

    def __init__(self, status):
        self.status = status
        self.error_message = None


@pytest.mark.parametrize("status", ["posted", "failed", "cancelled", "rejected"])
def test_terminal_records_cannot_be_cancelled(status):
    from backend.services.connections import publish_service

    with pytest.raises(ValueError):
        publish_service.cancel(_Record(status))


def test_a_publish_already_being_sent_cannot_be_cancelled():
    """Flipping a mid-flight record would report a cancellation that never happened."""
    from backend.services.connections import publish_service

    with pytest.raises(ValueError, match="already being sent"):
        publish_service.cancel(_Record("processing"))


@pytest.mark.parametrize("status", ["posted", "failed", "cancelled", "rejected", "processing"])
def test_reject_is_refused_once_the_decision_has_passed(status):
    from backend.services.connections import publish_service

    with pytest.raises(ValueError):
        publish_service.reject(_Record(status), "no")
