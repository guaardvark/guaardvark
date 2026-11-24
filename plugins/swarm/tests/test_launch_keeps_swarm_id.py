"""launch() keeps the swarm id launch_async already handed to the caller."""

import pytest

import service.orchestrator as orch
from service.config import SwarmConfig


def _orchestrator(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return orch.SwarmOrchestrator(repo, SwarmConfig(enable_diagnostic_agent=False))


def test_launch_keeps_an_issued_swarm_id(tmp_path):
    o = _orchestrator(tmp_path)
    o.swarm_id = "swarm-issued-by-launch-async"
    empty_plan = tmp_path / "plan.md"
    empty_plan.write_text("# nothing here\n")
    with pytest.raises(ValueError):
        o.launch(empty_plan)
    assert o.swarm_id == "swarm-issued-by-launch-async"


def test_launch_issues_an_id_when_none_was_given(tmp_path):
    o = _orchestrator(tmp_path)
    empty_plan = tmp_path / "plan.md"
    empty_plan.write_text("# nothing here\n")
    with pytest.raises(ValueError):
        o.launch(empty_plan)
    assert o.swarm_id
