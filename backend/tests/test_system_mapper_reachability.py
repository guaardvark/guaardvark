"""A2 tests: ${BASE_URL} / template-literal / apiClient caller resolution.

On-disk fake repo. Verifies a resolved caller matches a real route (no ghost)
and an unmatched ${BASE_URL} path is flagged as a ghost caller.
"""
from pathlib import Path

from backend.services.system_mapper import reachability
from backend.services.system_mapper.core import FindingKind


def _write(p: Path, text: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _fake_repo(tmp_path: Path) -> Path:
    _write(
        tmp_path / "frontend" / "src" / "api" / "apiClient.js",
        'export const BASE_URL = (import.meta.env.VITE_API_BASE_URL || "/api")'
        '.replace(/\\/$/, "");\n',
    )
    # A service that calls a real route and a ghost route via ${BASE_URL}.
    _write(
        tmp_path / "frontend" / "src" / "api" / "widgetService.js",
        "import { BASE_URL } from './apiClient';\n"
        "export const getWidgets = () => fetch(`${BASE_URL}/widgets`);\n"
        "export const getGhost = () => fetch(`${BASE_URL}/ghost`);\n",
    )
    # Backend blueprint mounting /widgets under /api (route exists).
    _write(
        tmp_path / "backend" / "api" / "widgets_api.py",
        "from flask import Blueprint\n"
        "bp = Blueprint('widgets', __name__, url_prefix='/api')\n"
        "@bp.route('/widgets', methods=['GET'])\n"
        "def list_widgets():\n    return {}\n",
    )
    return tmp_path


def test_base_url_default_read(tmp_path):
    _fake_repo(tmp_path)
    assert reachability._read_base_url_default(tmp_path) == "/api"


def test_base_url_missing_defaults_to_api(tmp_path):
    assert reachability._read_base_url_default(tmp_path) == "/api"


def test_resolve_js_url():
    assert reachability._resolve_js_url("${BASE_URL}/widgets", "/api") == "/api/widgets"
    # double /api/api collapses
    assert reachability._resolve_js_url("${BASE_URL}/api/x", "/api") == "/api/x"
    # other ${VAR} preserved for later normalization
    assert reachability._resolve_js_url("${BASE_URL}/u/${id}", "/api") == "/api/u/${id}"


def test_base_url_caller_resolves_no_ghost(tmp_path):
    root = _fake_repo(tmp_path)
    result = reachability.analyze(root)
    ghosts = {f.evidence.get("route") for f in result["findings"]
              if f.kind == FindingKind.GHOST_ENDPOINT}
    # /api/widgets has a resolved caller → not a ghost endpoint
    assert "/api/widgets" not in ghosts


def test_ghost_caller_for_unmatched_base_url_path(tmp_path):
    root = _fake_repo(tmp_path)
    result = reachability.analyze(root)
    ghost_callers = {f.evidence.get("path") for f in result["findings"]
                     if f.kind == FindingKind.GHOST_API_CALLER}
    assert "/api/ghost" in ghost_callers


def test_tree_without_frontend_sources_reports_no_ghosts(tmp_path):
    # The backend container of a Docker install: routes, no frontend/src.
    _write(
        tmp_path / "backend" / "api" / "widgets_api.py",
        "from flask import Blueprint\n"
        "bp = Blueprint('widgets', __name__, url_prefix='/api')\n"
        "@bp.route('/widgets', methods=['GET'])\n"
        "def list_widgets():\n    return {}\n",
    )
    result = reachability.analyze(tmp_path)
    kinds = [f.kind for f in result["findings"]]
    assert FindingKind.GHOST_ENDPOINT not in kinds
    assert FindingKind.GHOST_API_CALLER not in kinds
    assert kinds.count(FindingKind.FRONTEND_NOT_MAPPED) == 1
    assert result["stats"]["frontend_sources"] is False
    assert result["stats"]["ghost_endpoints_total"] == 0
    assert result["stats"]["backend_routes"] == 1
    assert result["graph"]["frontend_sources"] is False


def test_tree_with_frontend_sources_has_no_not_mapped_note(tmp_path):
    result = reachability.analyze(_fake_repo(tmp_path))
    assert result["stats"]["frontend_sources"] is True
    assert not [f for f in result["findings"] if f.kind == FindingKind.FRONTEND_NOT_MAPPED]
