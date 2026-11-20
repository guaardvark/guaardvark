"""Docker seeds the tracked data defaults onto the data volume.

/app/data is a volume, so the defaults a native install has in its checkout
(agent self-knowledge, recipes, style recipes, ...) reach a container only
through docker/seed_data.py. The seed must never overwrite a file the install
changed, and the image must carry the tracked files and nothing else.
"""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("seed_data", ROOT / "docker" / "seed_data.py")
seed_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_data)


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def dirs(tmp_path):
    dist, live = tmp_path / "dist", tmp_path / "live"
    _write(dist, "agent/self_knowledge.md", "v1 knowledge")
    _write(dist, "agent/recipes.json", '{"a": 1}')
    return dist, live


def _seed(dist, live):
    lines = []
    return seed_data.seed(dist, live, log=lines.append), lines


def test_missing_files_are_added(dirs):
    dist, live = dirs
    result, _ = _seed(dist, live)
    assert result["added"] == ["agent/recipes.json", "agent/self_knowledge.md"]
    assert (live / "agent/self_knowledge.md").read_text() == "v1 knowledge"
    state = json.loads((live / seed_data.STATE).read_text())["files"]
    assert state["agent/self_knowledge.md"]["seeded"] == seed_data.sha256(dist / "agent/self_knowledge.md")


def test_second_run_changes_nothing(dirs):
    dist, live = dirs
    _seed(dist, live)
    result, lines = _seed(dist, live)
    assert result == {"added": [], "updated": [], "kept": []}
    assert lines == []


def test_untouched_default_follows_a_newer_image(dirs):
    dist, live = dirs
    _seed(dist, live)
    _write(dist, "agent/self_knowledge.md", "v2 knowledge")
    result, _ = _seed(dist, live)
    assert result["updated"] == ["agent/self_knowledge.md"]
    assert (live / "agent/self_knowledge.md").read_text() == "v2 knowledge"
    # and it is still tracked as untouched for the image after that
    _write(dist, "agent/self_knowledge.md", "v3 knowledge")
    assert _seed(dist, live)[0]["updated"] == ["agent/self_knowledge.md"]


def test_changed_file_is_kept_and_reported_once_per_newer_default(dirs):
    dist, live = dirs
    _seed(dist, live)
    _write(live, "agent/self_knowledge.md", "what this machine learned")

    result, lines = _seed(dist, live)
    assert result == {"added": [], "updated": [], "kept": []} and lines == []

    _write(dist, "agent/self_knowledge.md", "v2 knowledge")
    result, lines = _seed(dist, live)
    assert result["kept"] == ["agent/self_knowledge.md"] and result["updated"] == []
    assert len(lines) == 1 and "agent/self_knowledge.md" in lines[0]
    assert _seed(dist, live)[1] == []
    assert (live / "agent/self_knowledge.md").read_text() == "what this machine learned"


def test_file_already_on_the_volume_without_a_record_is_kept(dirs):
    dist, live = dirs
    _write(live, "agent/recipes.json", '{"mine": 1}')
    result, _ = _seed(dist, live)
    assert result["added"] == ["agent/self_knowledge.md"]
    assert result["kept"] == ["agent/recipes.json"]
    assert (live / "agent/recipes.json").read_text() == '{"mine": 1}'
    _write(dist, "agent/recipes.json", '{"a": 2}')
    _seed(dist, live)
    assert (live / "agent/recipes.json").read_text() == '{"mine": 1}'


def test_file_identical_to_the_default_is_adopted_as_untouched(dirs):
    dist, live = dirs
    _write(live, "agent/recipes.json", '{"a": 1}')
    _seed(dist, live)
    _write(dist, "agent/recipes.json", '{"a": 2}')
    assert _seed(dist, live)[0]["updated"] == ["agent/recipes.json"]


def test_deleted_default_comes_back(dirs):
    dist, live = dirs
    _seed(dist, live)
    (live / "agent/recipes.json").unlink()
    assert _seed(dist, live)[0]["added"] == ["agent/recipes.json"]


def test_nothing_is_removed_when_a_default_stops_shipping(dirs):
    dist, live = dirs
    _write(live, "uploads/report.pdf", "user file")
    _seed(dist, live)
    (dist / "agent/recipes.json").unlink()
    _seed(dist, live)
    assert (live / "agent/recipes.json").exists()
    assert (live / "uploads/report.pdf").read_text() == "user file"


def test_unreadable_record_is_treated_as_no_record(dirs):
    dist, live = dirs
    _seed(dist, live)
    _write(live, "agent/self_knowledge.md", "edited")
    (live / seed_data.STATE).write_text("not json")
    _seed(dist, live)
    assert (live / "agent/self_knowledge.md").read_text() == "edited"


def _data_exceptions() -> list:
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    return [line[1:] for line in lines if line.startswith("!data/")]


def test_dockerignore_admits_only_named_data_files():
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    assert "data/**" in lines
    exceptions = _data_exceptions()
    assert exceptions and not any(set("*?[") & set(e) or e.endswith("/") for e in exceptions)
    assert all(lines.index("data/**") < lines.index("!" + e) for e in exceptions)


def test_dockerignore_data_list_matches_the_tracked_files():
    if not (ROOT / ".git").exists() or shutil.which("git") is None:
        pytest.skip("not a git checkout")
    tracked = subprocess.run(
        ["git", "ls-files", "data"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert sorted(_data_exceptions()) == sorted(tracked)


def test_image_ships_and_runs_the_seed():
    dockerfile = (ROOT / "docker" / "Dockerfile.backend").read_text()
    assert "COPY data/ /app/.data-dist/" in dockerfile
    assert "docker/seed_data.py" in dockerfile
    assert "python /app/docker/seed_data.py" in (ROOT / "docker" / "entrypoint.sh").read_text()
