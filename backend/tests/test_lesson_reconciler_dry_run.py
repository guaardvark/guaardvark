"""The lesson reconciler's dry run previews exactly what a real run stages:
active rows only, editable knowledge files only, no group a settled proposal
already answers, no line that is already hedged, no recipe claim that no single
recipe's click step names."""
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask
from backend.models import db, AgentMemory, PendingFix
from backend.services import lesson_reconciler

REPO = Path(__file__).resolve().parents[2]
HEDGE = "  <!-- belief-update: 3 sessions did not see this; verify before assuming -->"


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = tmp_path / "data" / "agent"
    root.mkdir(parents=True)
    lines = [f"- filler line {i}\n" for i in range(1, 61)]
    lines[9] = "- The Dock is along the bottom of the screen.\n"
    lines[29] = "- The Settings gear is in the top-right corner.\n"
    lines[39] = "- The Terminal opens from the Dock." + HEDGE + "\n"
    lines[49] = "- The Shortcuts panel is on the left edge of the window.\n"
    (root / "self_knowledge_compact.md").write_text("".join(lines))
    (root / "recipes.json").write_text('{\n  "open firefox": {"steps": ["click the Firefox icon"]}\n}\n')
    monkeypatch.setattr(lesson_reconciler, "_knowledge_root", lambda: str(root))

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        _seed(root)
        yield app
        db.session.remove()
        db.drop_all()


def _belief(element, src, count, status="active"):
    for _ in range(count):
        db.session.add(AgentMemory(
            id=os.urandom(16).hex(),
            content=f"{element} was not visible",
            type="belief_update",
            source="agent",
            status=status,
            tags=json.dumps(["belief_update", element, f"src:{src}"]),
        ))


def _seed(root):
    _belief("shortcuts panel", "self_knowledge_compact.md:50", 3)
    _belief("dock", "self_knowledge_compact.md:10", 4)
    # Two active rows are below the threshold; the archived ones are retired evidence.
    _belief("files icon", "self_knowledge_compact.md:20", 2)
    _belief("files icon", "self_knowledge_compact.md:20", 2, status="archived")
    # No recipe's click step names the icon, and model_belief rows have no file line.
    _belief("firefox icon", "recipes.json:2", 5)
    _belief("purple unicorn", "model_belief", 3)
    # A person already rejected this one.
    _belief("settings gear", "self_knowledge_compact.md:30", 3)
    db.session.add(PendingFix(
        file_path=str(root / "self_knowledge_compact.md"),
        proposed_diff="-\n+\n",
        fix_description="'settings gear' flagged as not-visible across 3 sessions.",
        status="rejected",
    ))
    # Already hedged in the file.
    _belief("terminal", "self_knowledge_compact.md:40", 3)
    db.session.commit()


def test_plan_applies_every_filter_and_says_why(app):
    plan = lesson_reconciler.plan_belief_updates(threshold=3)

    assert {(c.element, c.source_line) for c in plan.candidates} == {
        ("shortcuts panel", 50), ("dock", 10),
    }
    assert {(s.element, s.reason) for s in plan.skipped} == {
        ("firefox icon", "no single recipe clicks it, or it already has a note"),
        ("purple unicorn", "not an editable knowledge file"),
        ("settings gear", "already proposed, applied or rejected"),
        ("terminal", "line unreadable, blank or already hedged"),
    }
    assert plan.memories == 3 + 4 + 2 + 5 + 3 + 3 + 3
    assert PendingFix.query.count() == 1  # planning writes nothing


def test_a_real_run_stages_exactly_the_planned_candidates(app):
    planned = {(c.element, c.source_line) for c in lesson_reconciler.plan_belief_updates(3).candidates}

    staged = lesson_reconciler.stage_belief_updates(3)

    assert {(c.element, c.source_line) for c in staged} == planned
    new_rows = PendingFix.query.filter_by(status="proposed").all()
    assert sorted(r.fix_description.split(" flagged")[0] for r in new_rows) == ["'dock'", "'shortcuts panel'"]
    assert lesson_reconciler.plan_belief_updates(3).candidates == []


def test_one_proposal_per_element_and_file_even_across_lines(app):
    _belief("shortcuts panel", "self_knowledge_compact.md:49", 3)
    db.session.commit()

    plan = lesson_reconciler.plan_belief_updates(3)

    assert [c.element for c in plan.candidates].count("shortcuts panel") == 1
    assert ("shortcuts panel", "already proposed, applied or rejected") in {
        (s.element, s.reason) for s in plan.skipped
    }
    assert lesson_reconciler.scan_belief_updates(3) == len(plan.candidates)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "run_lesson_reconciler_under_test", REPO / "scripts" / "run_lesson_reconciler.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _listed(out, header):
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(header)) + 1
    listed = []
    for line in lines[start:]:
        if not line.startswith("  - "):
            break
        listed.append(line)
    return listed


def test_cli_dry_run_and_real_run_list_the_same_candidates(app, monkeypatch, capsys):
    script = _load_script()
    # The script builds its app at call time from backend.app.create_app.
    fake_app_module = types.ModuleType("backend.app")
    fake_app_module.create_app = lambda: app
    monkeypatch.setitem(sys.modules, "backend.app", fake_app_module)

    assert script.main(["--dry-run"]) == 0
    dry = capsys.readouterr().out
    assert PendingFix.query.count() == 1

    assert script.main([]) == 0
    real = capsys.readouterr().out

    would = _listed(dry, "Would stage")
    assert would == _listed(real, "Lesson reconciler staged")
    assert would == [
        "  - 4x: 'dock' @ self_knowledge_compact.md:10",
        "  - 3x: 'shortcuts panel' @ self_knowledge_compact.md:50",
    ]
    assert "  - 5x: 'firefox icon' @ recipes.json (no single recipe clicks it, or it already has a note)" in dry
    assert "  - 3x: 'purple unicorn' @ model_belief (not an editable knowledge file)" in dry
    assert "files icon" not in dry
