"""Named subsystems: the definitions file, which subsystem claims a file, and
the report of files none claims."""
import json
from pathlib import Path

from backend.services.system_mapper import codebase_map, subsystems
from backend.services.system_mapper.core import FindingKind

DEFINITIONS = Path(__file__).resolve().parents[1] / "services" / "system_mapper" / "subsystems.json"


def test_the_shipped_definitions_are_well_formed():
    data = json.loads(DEFINITIONS.read_text())
    names = [s["name"] for s in data["subsystems"]]
    assert len(names) == len(set(names)) >= 30
    for s in data["subsystems"]:
        assert s["description"].strip() and "\n" not in s["description"]
        assert s["globs"] and all(isinstance(g, str) and not g.startswith("/") for g in s["globs"])


def test_the_most_specific_glob_wins():
    defs = [
        {"name": "tools", "description": "", "globs": ["backend/tools/*"]},
        {"name": "code intelligence", "description": "", "globs": ["backend/tools/code_search_tools.py"]},
    ]
    assert subsystems.assign("backend/tools/code_search_tools.py", defs) == "code intelligence"
    assert subsystems.assign("backend/tools/image_tools.py", defs) == "tools"
    assert subsystems.assign("frontend/src/App.jsx", defs) is None


def test_find_accepts_loose_names():
    defs = [{"name": "chat engine", "description": "", "globs": ["x"]},
            {"name": "film crew", "description": "", "globs": ["y"]}]
    assert subsystems.find("Chat engine", defs)["name"] == "chat engine"
    assert subsystems.find("film-crew", defs)["name"] == "film crew"
    assert subsystems.find("crew", defs)["name"] == "film crew"
    assert subsystems.find("swarm", defs) is None


def _write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_unclaimed_files_are_reported_once_and_nodes_carry_their_subsystem(tmp_path):
    _write(tmp_path, "backend/services/system_mapper/subsystems.json", json.dumps({"subsystems": [
        {"name": "core", "description": "The core.", "globs": ["backend/core/*"]}]}))
    _write(tmp_path, "backend/core/a.py", "x = 1\n")
    _write(tmp_path, "backend/other/b.py", "y = 2\n")
    smap = codebase_map(tmp_path)
    assert smap.node_meta["backend.core.a"]["subsystem"] == "core"
    assert smap.node_meta["backend.other.b"]["subsystem"] is None
    unclaimed = [f for f in smap.findings if f.kind == FindingKind.NO_SUBSYSTEM]
    assert len(unclaimed) == 1
    assert "backend/other/b.py" in unclaimed[0].evidence["sample"]
    assert smap.subsystems["core"]["files"] == 1


def test_a_tree_without_definitions_has_no_subsystems(tmp_path):
    _write(tmp_path, "backend/a.py", "x = 1\n")
    smap = codebase_map(tmp_path)
    assert smap.subsystems == {}
    assert not [f for f in smap.findings if f.kind == FindingKind.NO_SUBSYSTEM]
