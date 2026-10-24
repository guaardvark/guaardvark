"""A project folder's import graph and repository map come from the files on disk."""
import os
import time

from backend import config
from backend.services.repository_analysis_service import project_code_map


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_graph_and_map_from_disk_and_rebuilt_when_source_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path / "storage"))
    root = tmp_path / "project"
    _write(root, "service/firewall.py", "def validate_block(ip):\n    return ip\n")
    _write(root, "tests/test_firewall.py", "from service.firewall import validate_block\n")
    _write(root, "web/app.js", "import { helper } from './util';\nhelper();\n")
    _write(root, "web/util.js", "export function helper() { return 1; }\n")
    _write(root, "node_modules/pkg/index.js", "export const ignored = 1;\n")
    _write(root, ".hidden/secret.py", "def hidden():\n    pass\n")

    data = project_code_map("project-1", str(root))

    assert data["dependency_graph"]["tests/test_firewall.py"] == ["service/firewall.py"]
    assert data["dependency_graph"]["web/app.js"] == ["web/util.js"]
    assert "## service/firewall.py" in data["repository_map"]
    assert not any(p.startswith(("node_modules/", ".hidden/")) for p in data["dependency_graph"])
    assert data["file_count"] == 4

    again = project_code_map("project-1", str(root))
    assert again["analyzed_at"] == data["analyzed_at"]

    newer = _write(root, "service/rules.py", "from service.firewall import validate_block\n")
    future = time.time() + 5
    os.utime(newer, (future, future))
    rebuilt = project_code_map("project-1", str(root))
    assert rebuilt["file_count"] == 5
    assert rebuilt["dependency_graph"]["service/rules.py"] == ["service/firewall.py"]
