"""Findings: stable ids, frontend URL resolution, dispatch without duplicates,
and the runtime-liveness records the findings depend on.

On-disk fake repos (tmp_path, outside any git checkout); the self-improvement
service and the database are stand-ins where they appear.
"""
from pathlib import Path

import pytest

from backend.services.system_mapper import actions, reachability
from backend.services.system_mapper.core import Finding, FindingKind, Severity


def _write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# ---- stable ids ----------------------------------------------------------------

def test_id_ignores_counts_in_the_summary():
    a = Finding(FindingKind.OVER_COUPLED, Severity.MEDIUM, "x sits on 7 short import cycles",
                ["backend/x.py"], {"key": "backend.x"})
    b = Finding(FindingKind.OVER_COUPLED, Severity.LOW, "x sits on 9 short import cycles",
                ["backend/x.py"], {"key": "backend.x"})
    assert a.fingerprint() == b.fingerprint()


def test_id_changes_with_kind_paths_or_key():
    base = Finding(FindingKind.GHOST_ENDPOINT, Severity.LOW, "s", ["a.py"], {"key": "/api/a"})
    assert base.fingerprint() != Finding(FindingKind.GHOST_API_CALLER, Severity.LOW, "s", ["a.py"],
                                         {"key": "/api/a"}).fingerprint()
    assert base.fingerprint() != Finding(FindingKind.GHOST_ENDPOINT, Severity.LOW, "s", ["b.py"],
                                         {"key": "/api/a"}).fingerprint()
    assert base.fingerprint() != Finding(FindingKind.GHOST_ENDPOINT, Severity.LOW, "s", ["a.py"],
                                         {"key": "/api/b"}).fingerprint()


def test_every_analyzer_finding_carries_a_key(tmp_path):
    from backend.services.system_mapper import codebase_map

    _write(tmp_path, "backend/a.py", "from backend import b\n")
    _write(tmp_path, "backend/b.py", "from backend import a\n")
    _write(tmp_path, "backend/lonely.py", "def never_called_helper_xyz():\n    return 1\n")
    _write(tmp_path, "backend/old.py.BACK", "")
    _write(tmp_path, "backend/api/x_api.py",
           "from flask import Blueprint\nbp = Blueprint('x', __name__, url_prefix='/api/x')\n"
           "@bp.route('/ping')\ndef ping():\n    return {}\n")
    _write(tmp_path, "frontend/src/api/x.js", "fetch('/api/nowhere');\n")
    smap = codebase_map(tmp_path)
    assert smap.findings
    assert all("key" in f.evidence for f in smap.findings), [f.summary for f in smap.findings
                                                            if "key" not in f.evidence]


# ---- frontend URLs -----------------------------------------------------------------

def _frontend(root: Path, files: dict) -> None:
    _write(root, "frontend/src/api/apiClient.js",
           'export const BASE_URL = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\\/$/, "");\n')
    for rel, text in files.items():
        _write(root, rel, text)
    _write(root, "backend/api/plugins_api.py",
           "from flask import Blueprint\n"
           "bp = Blueprint('plugins', __name__, url_prefix='/api/plugins')\n"
           "@bp.route('')\ndef list_plugins():\n    return {}\n"
           "@bp.route('/<plugin_id>/start', methods=['POST'])\ndef start(plugin_id):\n    return {}\n"
           "@bp.route('/code-execution/python', methods=['POST'])\ndef py():\n    return {}\n")
    _write(root, "backend/api/outreach_api.py",
           "from flask import Blueprint\n"
           "bp = Blueprint('o', __name__, url_prefix='/api/social-outreach')\n"
           "@bp.route('/status')\ndef status():\n    return {}\n")


def _ghost_callers(root: Path) -> set:
    result = reachability.analyze(root)
    return {f.evidence["path"] for f in result["findings"] if f.kind == FindingKind.GHOST_API_CALLER}


def test_a_file_with_its_own_base_url_resolves_against_it(tmp_path):
    _frontend(tmp_path, {"frontend/src/api/pluginsService.js": (
        'import { BASE_URL as API_BASE_URL, handleResponse } from "./apiClient";\n'
        "const BASE_URL = `${API_BASE_URL}/plugins`;\n"
        "export const listPlugins = async () => fetch(BASE_URL, { method: 'GET' });\n"
        "export const startPlugin = async (id) => fetch(`${BASE_URL}/${id}/start`, { method: 'POST' });\n"
    )})
    assert _ghost_callers(tmp_path) == set()


def test_other_constants_are_substituted(tmp_path):
    _frontend(tmp_path, {"frontend/src/api/codeExecutionService.js": (
        'import { BASE_URL } from "./apiClient";\n'
        'const CODE_EXEC_BASE = "/plugins/code-execution";\n'
        "export const run = () => fetch(`${BASE_URL}${CODE_EXEC_BASE}/python`, { method: 'POST' });\n"
    )})
    assert _ghost_callers(tmp_path) == set()


def test_a_glued_tail_matches_as_a_prefix(tmp_path):
    _frontend(tmp_path, {"frontend/src/api/outreachService.js": (
        'const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";\n'
        "async function request(path) { return fetch(`${BASE_URL}/social-outreach${path}`); }\n"
        "export const plan = (p) => fetch(`${BASE_URL}/plugins${p ? \"?x=1\" : \"\"}`);\n"
    )})
    assert _ghost_callers(tmp_path) == set()


def test_test_files_are_not_callers(tmp_path):
    _frontend(tmp_path, {"frontend/src/api/__tests__/apiAuth.test.js": "fetch('/api/x');\n"})
    result = reachability.analyze(tmp_path)
    assert result["graph"]["callers"] == []


def test_a_url_with_no_fixed_segment_is_unresolved_not_a_ghost(tmp_path):
    _frontend(tmp_path, {"frontend/src/components/Panel.jsx": (
        'import { BASE_URL } from "../api/apiClient";\n'
        "export function load(path) { return fetch(`${BASE_URL}/${path}`); }\n"
    )})
    result = reachability.analyze(tmp_path)
    assert result["stats"]["unresolved_callers"] == 1
    assert not [f for f in result["findings"] if f.kind == FindingKind.GHOST_API_CALLER]


def test_a_real_ghost_is_still_reported(tmp_path):
    _frontend(tmp_path, {"frontend/src/api/x.js": (
        'import { BASE_URL } from "./apiClient";\nexport const g = () => fetch(`${BASE_URL}/plugins/missing/thing`);\n'
    )})
    assert _ghost_callers(tmp_path) == {"/api/plugins/missing/thing"}


def test_pages_are_linked_to_the_endpoints_they_reach(tmp_path):
    _frontend(tmp_path, {
        "frontend/src/api/pluginsService.js": (
            'import { BASE_URL as API } from "./apiClient";\n'
            "export const listPlugins = async () => {\n  return fetch(`${API}/plugins`);\n};\n"
            "export const startPlugin = async (id) => {\n  return fetch(`${API}/plugins/${id}/start`);\n};\n"
        ),
        "frontend/src/pages/PluginsPage.jsx": (
            'import { listPlugins } from "../api/pluginsService";\n'
            "export default function PluginsPage() {\n  listPlugins();\n  return null;\n}\n"
        ),
    })
    result = reachability.analyze(tmp_path)
    eps = result["graph"]["page_endpoints"]["frontend/src/pages/PluginsPage.jsx"]
    # Only the function the page uses, not every call in the service file.
    assert [e["path"] for e in eps] == ["/api/plugins"]
    assert eps[0]["served"] is True
    assert eps[0]["via"] == "frontend/src/api/pluginsService.js:3"


# ---- dispatch -----------------------------------------------------------------------

def test_unwired_tool_has_no_mechanical_fix_that_touches_core_tools():
    finding = {"id": "f1", "kind": "unwired-tool", "evidence": {"tool": "x_tool"}}
    assert actions.mechanical_proposal(finding) is None


def test_describe_names_the_finding_id():
    text = actions.describe({"id": "abc123", "kind": "ghost-api-caller", "severity": "medium",
                             "summary": "s", "paths": ["a.js"], "evidence": {}})
    assert "abc123" in text


def test_a_finding_is_not_dispatched_twice_while_its_fix_is_open(monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path))
    submitted = []

    class Svc:
        def submit_directed_task(self, **kwargs):
            submitted.append(kwargs)
            return {"success": True}

    monkeypatch.setattr("backend.services.self_improvement_service.get_self_improvement_service", lambda: Svc())
    finding = {"id": "f1", "kind": "backup-artifact", "severity": "high", "summary": "x.BACK",
               "paths": ["x.BACK"], "evidence": {}}
    root = tmp_path / "repo"
    first = actions.dispatch_finding(finding, root=root)
    second = actions.dispatch_finding(finding, root=root)
    assert first["success"] is True
    assert second["success"] is False and second["skipped"] is True
    assert "already" in second["reason"] or "dispatched" in second["reason"]
    assert len(submitted) == 1


def test_an_old_dispatch_with_no_open_fix_can_be_dispatched_again(monkeypatch, tmp_path):
    import json
    import time

    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path))
    root = tmp_path / "repo"
    finding = {"id": "f1", "kind": "backup-artifact", "paths": ["x.BACK"]}
    actions.record_dispatch(root, finding)
    ledger_path = actions._dispatched_path(root)
    ledger = json.loads(ledger_path.read_text())
    ledger["f1"]["at"] = time.time() - actions.DISPATCH_GRACE_S - 60
    ledger_path.write_text(json.dumps(ledger))
    # No database here: only the time window can hold it, and it has passed.
    assert actions.open_dispatch(root, finding) is None


# ---- liveness records -------------------------------------------------------------------

def test_celery_hits_name_the_module_that_defines_the_task():
    import inspect

    from backend import celery_app

    src = inspect.getsource(celery_app)
    assert "self.name.rsplit('.', 1)[0]" not in src
    assert 'getattr(run, "__module__", None)' in src


def test_web_process_route_hits_are_flushed_off_the_request_thread(monkeypatch):
    import threading

    from backend.services.execution_context_tracker import ExecutionContextTracker, MODE_FLASK_ROUTE

    tracker = ExecutionContextTracker()
    tracker.record_hit("route:bp.x", "route", "x", "backend.api.x_api", MODE_FLASK_ROUTE)
    flushed = threading.Event()
    seen = {}

    def fake_flush():
        seen["thread"] = threading.current_thread().name
        flushed.set()
        return 1

    monkeypatch.setattr(tracker, "flush", fake_flush)

    class App:
        def app_context(self):
            import contextlib
            return contextlib.nullcontext()

    tracker._last_flush_ts -= 120
    tracker.maybe_flush_async(App(), interval_s=60.0)
    assert flushed.wait(5)
    assert seen["thread"] == "liveness-flush"
    # Within the interval nothing more is started.
    flushed.clear()
    tracker.maybe_flush_async(App(), interval_s=60.0)
    assert not flushed.wait(0.2)


@pytest.mark.parametrize("kind", ["runtime-zombie", "contextual-discovery", "hot-path-spike", "dead-symbol"])
def test_advisory_kinds_stay_undispatchable(kind):
    assert kind not in actions.DISPATCHABLE_KINDS
