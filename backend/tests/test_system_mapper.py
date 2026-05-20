"""Tests for system_mapper: lifecycle/node_meta, the untested-module producer,
stable finding ids, and the findings action loop (rank/dismiss/describe).

Pure-logic — builds a tiny fake repo on disk, no DB/app fixtures.
"""
import json

from backend.services.system_mapper import codebase_map, actions
from backend.services.system_mapper.core import Finding, FindingKind, Severity


def _write(p, text=""):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _fake_repo(tmp_path):
    # A service with a test, and one without; a script; a backup artifact.
    # app_main imports both services so they have importers → lifecycle "active"
    # (a module nothing imports is correctly classed "dormant", not "untested").
    _write(tmp_path / "backend" / "services" / "tested_svc.py", "x = 1\n")
    _write(tmp_path / "backend" / "services" / "untested_svc.py", "y = 2\n")
    _write(
        tmp_path / "backend" / "services" / "app_main.py",
        "from backend.services import tested_svc, untested_svc\n",
    )
    _write(tmp_path / "backend" / "tests" / "test_tested_svc.py", "def test_x():\n    pass\n")
    _write(tmp_path / "scripts" / "do_thing.py", "z = 3\n")
    _write(tmp_path / "backend" / "services" / "old_svc.py.BACK", "dead = 1\n")
    return tmp_path


# ---- lifecycle + node_meta -----------------------------------------

def test_node_meta_lifecycle_classification(tmp_path):
    smap = codebase_map(_fake_repo(tmp_path))
    nm = smap.node_meta
    by_path = {m["path"]: m["lifecycle"] for m in nm.values()}
    assert by_path["backend/services/tested_svc.py"] == "active"
    assert by_path["backend/tests/test_tested_svc.py"] == "test"
    assert by_path["scripts/do_thing.py"] == "script"
    assert by_path["backend/services/untested_svc.py"] == "active"  # imported, no test
    # app_main imports the services but nothing imports it → dormant
    assert by_path["backend/services/app_main.py"] == "dormant"


def test_node_meta_in_serialized_dict(tmp_path):
    d = codebase_map(_fake_repo(tmp_path)).to_dict()
    assert "node_meta" in d and d["node_meta"]
    sample = next(iter(d["node_meta"].values()))
    assert {"lifecycle", "importers", "path"} <= set(sample)


# ---- untested-module producer (previously a dead enum) -------------

def test_untested_module_finding_emitted(tmp_path):
    smap = codebase_map(_fake_repo(tmp_path))
    untested = [f for f in smap.findings if f.kind == FindingKind.UNTESTED_MODULE]
    paths = {p for f in untested for p in f.paths}
    assert "backend/services/untested_svc.py" in paths
    # the module that HAS a test_*.py is not flagged
    assert "backend/services/tested_svc.py" not in paths


# ---- stable finding ids --------------------------------------------

def test_fingerprint_stable_and_in_dict():
    f = Finding(kind=FindingKind.GHOST_ENDPOINT, severity=Severity.LOW,
                summary="x", paths=["b.py", "a.py"])
    # order-independent + severity-independent
    f2 = Finding(kind=FindingKind.GHOST_ENDPOINT, severity=Severity.HIGH,
                 summary="x", paths=["a.py", "b.py"])
    assert f.fingerprint() == f2.fingerprint()
    assert f.to_dict()["id"] == f.fingerprint()


# ---- action loop: rank / dismiss / describe ------------------------

def _snapshot_with(*findings):
    return {"findings": [f.to_dict() for f in findings]}


def test_ranked_findings_orders_by_severity():
    snap = _snapshot_with(
        Finding(FindingKind.DORMANT_MODULE, Severity.LOW, "low one", ["a.py"]),
        Finding(FindingKind.URL_PATH_COLLISION, Severity.HIGH, "high one", ["b.py"]),
        Finding(FindingKind.IMPORT_CYCLE, Severity.MEDIUM, "med one", ["c.py"]),
    )
    ranked = actions.ranked_findings(snap, "/tmp/whatever")
    assert [f["severity"] for f in ranked] == ["high", "medium", "low"]
    assert ranked[0]["dispatchable"] is True       # url-path-collision is dispatchable
    assert ranked[-1]["dispatchable"] is False      # dormant-module is not


def test_dismiss_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path))
    snap = _snapshot_with(
        Finding(FindingKind.URL_PATH_COLLISION, Severity.HIGH, "boom", ["x.py"]),
    )
    fid = snap["findings"][0]["id"]
    root = "/some/code/root"

    assert len(actions.ranked_findings(snap, root)) == 1
    actions.dismiss(root, fid)
    assert actions.ranked_findings(snap, root) == []                       # hidden
    assert len(actions.ranked_findings(snap, root, include_dismissed=True)) == 1
    assert actions.ranked_findings(snap, root, include_dismissed=True)[0]["dismissed"]
    actions.undismiss(root, fid)
    assert len(actions.ranked_findings(snap, root)) == 1                   # back

    # persisted to disk under the storage dir
    persisted = json.loads((tmp_path / "cache" / "system_map"
                            ).glob("*.dismissed.json").__next__().read_text())
    assert persisted == []  # undismissed leaves an empty list


def test_describe_includes_kind_paths_evidence():
    f = Finding(FindingKind.UNWIRED_TOOL, Severity.HIGH, "tool X unreachable",
                ["backend/tools/x.py"], {"tool": "X"})
    text = actions.describe(f.to_dict())
    assert "unwired-tool" in text and "backend/tools/x.py" in text and "X" in text
