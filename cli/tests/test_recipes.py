"""Agent recipe commands must work offline and reject malformed recipes."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from llx.commands.recipes import load_recipes, validate_recipes
from llx.main import app


runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]


def _recipe(action: str = "click") -> dict:
    step: dict = {"action": action}
    if action == "click":
        # The backend validator requires a target_description on click steps.
        step["target_description"] = "the test button"
    return {
        "description": "A test recipe.",
        "triggers": ["^test$"],
        "steps": [step],
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
    assert "Invalid JSON" in result.stderr


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
    assert "Unknown recipe: not-a-recipe" in result.stderr


def test_cli_action_allowlist_matches_backend():
    from llx.commands import recipes as recipes_mod

    backend = recipes_mod._backend_validator()
    if backend is None:
        pytest.skip("backend validator unavailable in this layout")

    assert recipes_mod.KNOWN_ACTIONS == set(backend.SUPPORTED_RECIPE_ACTIONS)


def test_validation_delegates_to_backend_rules():
    from llx.commands import recipes as recipes_mod

    if recipes_mod._backend_validator() is None:
        pytest.skip("backend validator unavailable in this layout")

    # Click coordinates pass the old lightweight CLI check but must be rejected
    # by the canonical backend rules the CLI now delegates to.
    payload = {
        "clicky": {
            "description": "d",
            "triggers": ["^click$"],
            "steps": [{"action": "click", "x": 1, "y": 2}],
        }
    }

    assert any("x/y" in error for error in recipes_mod.validate_recipes(payload))


def test_list_and_show_accept_file_option(tmp_path):
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({"sample": _recipe()}), encoding="utf-8")

    listed = runner.invoke(app, ["recipes", "list", "--file", str(path), "--json"])
    shown = runner.invoke(app, ["recipes", "show", "sample", "--file", str(path), "--json"])

    assert listed.exit_code == 0
    assert json.loads(listed.stdout)["data"]["recipes"][0]["name"] == "sample"
    assert shown.exit_code == 0
    assert json.loads(shown.stdout)["data"]["recipe"]["name"] == "sample"


def test_show_exposes_preconditions_and_proof_timeout(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(REPO_ROOT))

    result = runner.invoke(app, ["recipes", "show", "open_firefox", "--json"])

    assert result.exit_code == 0
    recipe = json.loads(result.stdout)["data"]["recipe"]
    assert recipe["preconditions"] == ["firefox_not_running"]
    assert "success_proof_timeout_s" in recipe
