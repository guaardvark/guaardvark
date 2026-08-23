"""Workstation chat tools — real service wrappers, not sketches.

Proves (1) natural-language pinning surfaces the tools, (2) execute() calls
the same modules the pages use, (3) failures are honest.
"""
from __future__ import annotations

import pytest

from backend.services.unified_chat_engine import (
    WORKSTATION_TOOLS,
    _pin_workstation_tools,
    match_workstation_direct,
    select_tools_for_context,
)


class TestWorkstationPinning:
    def _pin(self, message):
        extras = ["web_search", "system_command", "read_code"]
        return set(_pin_workstation_tools(message, [], list(WORKSTATION_TOOLS) + extras))

    @pytest.mark.parametrize("message", [
        "use the system mapper and tell me what's broken",
        "debug GPU issues, what's using VRAM",
        "review the logs for the last celery crash",
        "launch a coding swarm on this repo",
        "is self-improvement on? any pending fixes?",
    ])
    def test_nl_pins_workstation_family(self, message):
        pinned = self._pin(message)
        assert pinned, f"nothing pinned for {message!r}"
        assert pinned <= set(WORKSTATION_TOOLS)

    @pytest.mark.parametrize("message", [
        "play some music and turn up the volume",
        "what's the weather like today",
        "draft a reddit post about our launch",
    ])
    def test_unrelated_pins_nothing(self, message):
        assert self._pin(message) == set()

    def test_direct_intercept_for_unambiguous_nl(self):
        assert match_workstation_direct("debug GPU issues")[0] == "inspect_gpu"
        assert match_workstation_direct("use the system mapper")[0] == "map_codebase"
        assert match_workstation_direct("review the logs")[0] == "read_logs"
        assert match_workstation_direct("play some music") is None
        assert match_workstation_direct("launch a coding swarm to rewrite auth") is None

    def test_keyword_selector_includes_map_on_sysmap_phrase(self):
        names = list(WORKSTATION_TOOLS) + ["web_search", "system_command"]
        selected = select_tools_for_context("use the system mapper please", names)
        assert "map_codebase" in selected


class TestInspectGpuCallsLiveModules:
    def test_aggregates_lock_orchestrator_nvidia(self, monkeypatch):
        from backend.tools import workstation_tools as wt

        monkeypatch.setattr(wt, "_nvidia_smi", lambda: {"available": True, "gpus": [{"name": "Fake"}]})

        class Coord:
            def get_gpu_status(self):
                return {"owner": "ollama", "available": False}

        monkeypatch.setattr(
            "backend.services.gpu_resource_coordinator.get_gpu_coordinator",
            lambda: Coord(),
        )

        class Orch:
            def get_registry_snapshot(self):
                return {"vram": {"used_mb": 12}, "models": []}

        monkeypatch.setattr(
            "backend.services.gpu_memory_orchestrator.get_orchestrator",
            lambda: Orch(),
        )

        class Mgr:
            def list_plugins(self):
                return [{"id": "comfyui", "running": True, "status": "running", "port": 8188, "vram_estimate_mb": 6000}]

        monkeypatch.setattr(
            "backend.plugins.plugin_manager.get_plugin_manager",
            lambda: Mgr(),
        )

        result = wt.InspectGpuTool().execute()
        assert result.success
        assert result.output["lock"]["owner"] == "ollama"
        assert result.output["orchestrator"]["vram"]["used_mb"] == 12
        assert result.output["plugins"][0]["id"] == "comfyui"
        assert result.output["nvidia"]["available"] is True


class TestReadLogs:
    def test_tails_allowlisted_file(self, tmp_path, monkeypatch):
        from backend.tools import workstation_tools as wt

        log = tmp_path / "backend.log"
        log.write_text("alpha\nERROR boom\nomega\n", encoding="utf-8")
        monkeypatch.setattr(wt, "_log_dir", lambda: tmp_path)

        result = wt.ReadLogsTool().execute(name="backend.log", lines=20, query="error")
        assert result.success
        assert "ERROR boom" in result.output["text"]
        assert "alpha" not in result.output["text"]

    def test_rejects_unknown_name(self):
        from backend.tools.workstation_tools import ReadLogsTool

        result = ReadLogsTool().execute(name="../etc/passwd")
        assert not result.success
        assert "Unknown log" in result.error


class TestMapAndDispatch:
    def test_map_returns_ranked_findings_from_snapshot(self, monkeypatch):
        from backend.tools import workstation_tools as wt

        monkeypatch.setattr(wt, "_safe_root", lambda _arg: "/repo")
        snapshot = {
            "file_count": 3,
            "languages": ["python"],
            "stats": {"findings": 1},
            "findings": [{
                "id": "abc123",
                "kind": "unwired-tool",
                "severity": "medium",
                "summary": "tool X is registered but unused",
                "paths": ["backend/tools/x.py"],
            }],
            "_cache": {"hit": True},
        }
        monkeypatch.setattr(wt, "_load_snapshot", lambda root, refresh: snapshot)

        result = wt.MapCodebaseTool().execute()
        assert result.success
        assert result.output["file_count"] == 3
        assert result.output["findings"][0]["id"] == "abc123"
        assert result.output["findings"][0]["dispatchable"] is True

    def test_dispatch_refuses_when_precheck_fails(self, monkeypatch):
        from backend.tools import workstation_tools as wt

        monkeypatch.setattr(wt, "_safe_root", lambda _arg: "/repo")
        monkeypatch.setattr(wt, "_load_snapshot", lambda root, refresh: {
            "findings": [{"id": "abc123", "kind": "unwired-tool", "summary": "x", "paths": []}],
        })

        class Svc:
            def dispatch_precheck(self):
                return {"ok": False, "reason": "Codebase is locked"}

        monkeypatch.setattr(
            "backend.services.self_improvement_service.get_self_improvement_service",
            lambda: Svc(),
        )
        called = {"n": 0}

        def boom(*_a, **_k):
            called["n"] += 1
            raise AssertionError("dispatch_finding must not run when locked")

        monkeypatch.setattr("backend.services.system_mapper.actions.dispatch_finding", boom)
        result = wt.DispatchMapFindingTool().execute(finding_id="abc123")
        assert not result.success
        assert "locked" in result.error.lower()
        assert called["n"] == 0

    def test_dispatch_calls_real_action(self, monkeypatch):
        from backend.tools import workstation_tools as wt

        monkeypatch.setattr(wt, "_safe_root", lambda _arg: "/repo")
        monkeypatch.setattr(wt, "_load_snapshot", lambda root, refresh: {
            "findings": [{"id": "abc123", "kind": "unwired-tool", "summary": "x", "paths": ["a.py"]}],
        })

        class Svc:
            def dispatch_precheck(self):
                return {"ok": True, "reason": "ready"}

        monkeypatch.setattr(
            "backend.services.self_improvement_service.get_self_improvement_service",
            lambda: Svc(),
        )
        monkeypatch.setattr(
            "backend.services.system_mapper.actions.dispatch_finding",
            lambda finding, priority="medium": {"success": True, "change": {"file": finding["paths"][0]}},
        )
        result = wt.DispatchMapFindingTool().execute(finding_id="abc123")
        assert result.success
        assert result.output["change"]["file"] == "a.py"


class TestSwarmHonesty:
    def test_offline_is_an_error_not_empty_success(self, monkeypatch):
        from backend.api import swarm_api
        from backend.tools.workstation_tools import SwarmStatusTool

        monkeypatch.setattr(swarm_api, "_proxy_get", lambda path: ({"error": "Swarm service not running"}, 503))
        result = SwarmStatusTool().execute()
        assert not result.success
        assert "not running" in result.error.lower()

    def test_launch_uses_proxy_post(self, monkeypatch):
        from backend.api import swarm_api
        from backend.tools.workstation_tools import LaunchSwarmTool

        seen = {}

        def fake_post(path, json_data=None, timeout=10):
            seen["path"] = path
            seen["body"] = json_data
            return {"swarm_id": "s1"}, 200

        monkeypatch.setattr(swarm_api, "_proxy_post", fake_post)
        monkeypatch.setattr(
            "backend.services.guarded_code_service.default_repo_root",
            lambda: type("P", (), {"__str__": lambda self: "/repo"})(),
        )
        result = LaunchSwarmTool().execute(goal="add tests", acknowledge_dirty_tree=True)
        assert result.success
        assert seen["path"] == "/swarm/launch"
        assert seen["body"]["self_code"] is True
        assert seen["body"]["auto_merge"] is False


class TestSelfImprovementHonesty:
    def test_submit_refuses_when_locked(self, monkeypatch):
        from backend.tools.workstation_tools import SubmitImprovementTool

        class Svc:
            def dispatch_precheck(self):
                return {"ok": False, "reason": "Self-improvement is disabled"}

            def submit_directed_task(self, *a, **k):
                raise AssertionError("must not submit when disabled")

        monkeypatch.setattr(
            "backend.services.self_improvement_service.get_self_improvement_service",
            lambda: Svc(),
        )
        result = SubmitImprovementTool().execute(description="fix the mapper wiring")
        assert not result.success
        assert "disabled" in result.error.lower()


class TestRegistration:
    def test_register_workstation_tools_adds_all_names(self, monkeypatch):
        from backend.services import agent_tools
        from backend.tools import tool_registry_init

        monkeypatch.setattr(agent_tools, "_global_tool_registry", agent_tools.ToolRegistry())
        monkeypatch.setattr(tool_registry_init, "_tool_categories", {})
        registered = tool_registry_init.register_workstation_tools()
        assert set(WORKSTATION_TOOLS) <= set(registered)
        names = agent_tools.get_tool_registry().list_tools()
        for name in WORKSTATION_TOOLS:
            assert name in names
