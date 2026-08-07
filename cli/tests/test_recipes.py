"""Agent recipe commands must work offline and reject malformed recipes."""

import json
from pathlib import Path

from typer.testing import CliRunner

from llx.commands.recipes import load_recipes, validate_recipes
from llx.main import app


runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]


def _recipe(action: str = "click") -> dict:
    return {
        "description": "A test recipe.",
        "triggers": ["^test$"],
        "steps": [{"action": action}],
    }


def test_current_recipe_file_is_valid():
    payload = load_recipes(REPO_ROOT / "data" / "agent" / "recipes.json")
    assert validate_recipes(payload) == []


def test_validate_reports_required_fields_and_unknown_actions():
    payload = {
        "missing_triggers": {"description": "Missing triggers", "steps": [{"action": "click"}]},
        "empty_steps": {"description": "No steps", "triggers": ["^empty$"], "steps": []},
        "unknown_action": _recipe("launch_rocket"),
    }

    errors = validate_recipes(payload)

    assert any("missing_triggers" in error and "triggers" in error for error in errors)
    assert any("empty_steps" in error and "steps" in error for error in errors)
    assert any("unknown_action" in error and "launch_rocket" in error for error in errors)


def test_validate_rejects_invalid_json(tmp_path):
    path = tmp_path / "recipes.json"
    path.write_text("{not json", encoding="utf-8")

    result = runner.invoke(app, ["recipes", "validate", "--file", str(path)])

    assert result.exit_code == 1
    assert "Invalid JSON" in result.stdout


def test_validate_candidate_file_success(tmp_path):
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({"sample": _recipe()}), encoding="utf-8")

    result = runner.invoke(app, ["recipes", "validate", "--file", str(path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["data"]["recipe_count"] == 1


def test_list_and_show_work_with_backend_offline(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(REPO_ROOT))

    listed = runner.invoke(app, ["recipes", "list", "--json"])
    shown = runner.invoke(app, ["recipes", "show", "navigate_url", "--json"])

    assert listed.exit_code == 0
    assert shown.exit_code == 0
    assert json.loads(listed.stdout)["data"]["recipes"]
    details = json.loads(shown.stdout)["data"]["recipe"]
    assert details["name"] == "navigate_url"
    assert details["triggers"]
    assert details["step_count"] > 0


def test_show_unknown_recipe_is_clear(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(REPO_ROOT))

    result = runner.invoke(app, ["recipes", "show", "not-a-recipe"])

    assert result.exit_code == 1
    assert "Unknown recipe: not-a-recipe" in result.stdout
