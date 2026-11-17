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


def _blueprint(root, name, prefix, routes):
    body = (f"from flask import Blueprint\nbp = Blueprint('{name}', __name__, url_prefix='{prefix}')\n")
    for i, (path, methods) in enumerate(routes):
        body += f"@bp.route('{path}', methods={methods!r})\ndef h{i}():\n    return {{}}\n"
    p = root / "backend" / "api" / f"{name}_api.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _prefix_findings(root):
    result = reachability.analyze(root)
    return result, [f for f in result["findings"] if f.kind == FindingKind.URL_PREFIX_COLLISION]


def test_files_sharing_a_prefix_without_a_common_url_are_not_a_collision(tmp_path):
    _blueprint(tmp_path, "meta", "/api/meta", [("/status", ["GET"]), ("/jobs/<job_id>", ["DELETE"])])
    _blueprint(tmp_path, "cache", "/api/meta", [("/clear", ["POST"]), ("/jobs/active", ["GET"])])
    result, found = _prefix_findings(tmp_path)
    assert found == []
    assert result["stats"]["shared_prefixes"] == 1
    assert result["stats"]["prefix_collisions"] == 0


def test_a_variable_route_matching_another_files_fixed_path_is_a_collision(tmp_path):
    _blueprint(tmp_path, "docs", "/api/docs", [("/<doc_id>", ["GET"])])
    _blueprint(tmp_path, "query", "/api/docs", [("/search", ["GET"])])
    _result, found = _prefix_findings(tmp_path)
    assert len(found) == 1
    assert found[0].evidence["key"] == "/api/docs"
    assert sorted(found[0].paths) == ["backend/api/docs_api.py", "backend/api/query_api.py"]


def test_a_path_tail_route_collides_with_deeper_paths_in_another_file(tmp_path):
    _blueprint(tmp_path, "files", "/api/files", [("/raw/<path:name>", ["GET"])])
    _blueprint(tmp_path, "ops", "/api/files", [("/raw/thumb/<file_id>", ["GET"]), ("/list", ["GET"])])
    _result, found = _prefix_findings(tmp_path)
    assert len(found) == 1 and len(found[0].evidence["pairs"]) == 1

