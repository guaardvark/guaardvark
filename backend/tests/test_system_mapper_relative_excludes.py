"""The mapper's folder exclusions apply below the mapped root only.

A checkout that sits under a folder named data, build, env or tests (a server
mount such as /data/guaardvark) must map the same as one that does not. A
map_codebase root that is itself inside a skipped folder is refused, since
nothing under it would be mapped.
"""

from pathlib import Path

import pytest

from backend.services.system_mapper import dependency_graph, reachability
from backend.services.system_mapper.core import codebase_map, is_excluded


def _tree(base: Path) -> Path:
    root = base / "guaardvark"
    (root / "backend" / "api").mkdir(parents=True)
    (root / "backend" / "__init__.py").write_text("")
    (root / "backend" / "util.py").write_text("def helper():\n    return 1\n")
    (root / "backend" / "api" / "__init__.py").write_text("")
    (root / "backend" / "api" / "things_api.py").write_text(
        "from flask import Blueprint\n"
        "from backend.util import helper\n"
        "things_bp = Blueprint('things', __name__, url_prefix='/api/things')\n\n"
        "@things_bp.route('/list', methods=['GET'])\n"
        "def list_things():\n"
        "    return {'n': helper()}\n"
    )
    (root / "data").mkdir()
    (root / "data" / "ignored.py").write_text("x = 1\n")
    return root


def test_is_excluded_tests_only_the_part_below_root():
    root = Path("/srv/data/guaardvark")
    assert not is_excluded(root / "backend" / "app.py", root=root)
    assert is_excluded(root / "data" / "x.py", root=root)
    assert is_excluded(root / "backend" / "venv" / "x.py", root=root)
    # Without root every part counts, which suits a path already relative to it.
    assert is_excluded(Path("data/x.py"))
    assert not is_excluded(Path("backend/app.py"))


@pytest.mark.parametrize("parent", ["plain", "data", "build", "env", "tests"])
def test_dependency_graph_maps_the_same_under_any_parent(tmp_path, parent):
    root = _tree(tmp_path / parent)
    result = dependency_graph.analyze(root, frozenset())
    # backend/__init__, backend/util, backend/api/__init__, backend/api/things_api;
    # data/ignored.py stays excluded because data is below root.
    assert result["file_count"] == 4


@pytest.mark.parametrize("parent", ["plain", "tests", "data"])
def test_backend_routes_are_found_under_any_parent(tmp_path, parent):
    root = _tree(tmp_path / parent)
    routes = reachability._backend_routes(root, frozenset())
    assert [r["path"] for r in routes] == ["/api/things/list"]


def test_codebase_map_file_count_matches_under_a_data_parent(tmp_path):
    plain = codebase_map(_tree(tmp_path / "plain"))
    under_data = codebase_map(_tree(tmp_path / "data"))
    assert plain.file_count == under_data.file_count == 4


def test_map_root_inside_a_skipped_folder_is_refused(monkeypatch, tmp_path):
    from backend.tools import workstation_tools as wt

    (tmp_path / "data" / "uploads" / "repo").mkdir(parents=True)
    (tmp_path / "backend" / "api").mkdir(parents=True)
    monkeypatch.setattr(wt, "_repo_root", lambda: tmp_path.resolve())
    with pytest.raises(ValueError, match="'data'"):
        wt._safe_root("data/uploads/repo")
    assert wt._safe_root("backend/api") == (tmp_path.resolve() / "backend" / "api")
