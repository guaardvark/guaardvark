"""The Code Editor's Format, Debug and validate paths say what they really did.

Before: Format and Debug shelled out to black/prettier/flake8/eslint whether or
not they were installed and reported success with the code unchanged, and
/validate sent the file to the model, discarded the answer and returned empty
error lists. These tests pin the honest behaviour: a missing tool is a 501 with
an install hint, JSON formats through the standard library, and syntax errors
come from a real parser with a line number.
"""
import shutil

import pytest
from flask import Flask

from backend.services import code_syntax_check
from backend.services.code_syntax_check import check_file_syntax, check_syntax, parse_node_check_error


# --- the syntax checker itself -----------------------------------------------

def test_python_syntax_error_has_the_right_line():
    result = check_syntax("x = 1\ndef f(:\n    pass\n", "python")
    assert result["supported"] is True
    assert result["tool"] == "python-ast"
    assert len(result["errors"]) == 1
    assert result["errors"][0]["line"] == 2


def test_clean_python_has_no_errors():
    result = check_syntax("def f():\n    return 1\n", "python")
    assert result["errors"] == []


def test_json_error_is_reported():
    result = check_syntax('{"a": 1,}', "json")
    assert result["supported"] is True
    assert result["errors"][0]["line"] == 1


def test_unknown_language_is_unsupported_not_clean():
    result = check_syntax("whatever", "cobol")
    assert result["supported"] is False
    assert result["errors"] == []
    assert "cobol" in result["note"]


def test_javascript_without_node_is_unsupported(monkeypatch):
    monkeypatch.setattr(code_syntax_check, "esbuild_path", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = check_syntax("let x = ;", "javascript")
    assert result["supported"] is False
    assert "node" in result["note"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_javascript_syntax_error_through_node(monkeypatch):
    monkeypatch.setattr(code_syntax_check, "esbuild_path", lambda: None)
    result = check_syntax("let x = ;\n", "javascript")
    assert result["supported"] is True
    assert result["tool"] == "node --check"
    assert result["errors"] and result["errors"][0]["line"] == 1
    assert check_syntax("let x = 1;\n", "javascript")["errors"] == []


def test_parse_node_check_error_reads_line_and_caret():
    stderr = "/tmp/x.js:3\nlet x = ;\n        ^\n\nSyntaxError: Unexpected token ';'\n"
    issue = parse_node_check_error(stderr, "/tmp/x.js")
    assert issue["line"] == 3
    assert issue["column"] == 9
    assert issue["message"].startswith("SyntaxError")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_a_broken_es_module_is_not_passed_as_valid(monkeypatch):
    # `node --check` on a .js file reads it as CommonJS and exits 0 for this.
    monkeypatch.setattr(code_syntax_check, "esbuild_path", lambda: None)
    result = check_file_syntax("module.js", 'import os from "os";\nconst a = = 1;\n')
    assert result["supported"] is True
    assert result["errors"]


def test_a_file_type_nothing_can_parse_is_not_checked(monkeypatch):
    monkeypatch.setattr(code_syntax_check, "esbuild_path", lambda: None)
    assert check_file_syntax("Page.jsx", "const a = <div/>;")["supported"] is False
    assert check_file_syntax("notes.md", "# hi")["supported"] is False


# --- the routes ------------------------------------------------------------------

@pytest.fixture()
def exec_client():
    from backend.api.code_execution_api import code_exec_bp
    app = Flask(__name__)
    app.register_blueprint(code_exec_bp)
    return app.test_client()


@pytest.fixture()
def intel_client():
    import backend.api.code_intelligence_api as ci
    app = Flask(__name__)
    app.register_blueprint(ci.code_intelligence_bp)
    return app.test_client()


def test_format_without_black_is_a_501_with_install_hint(exec_client, monkeypatch):
    import backend.api.code_execution_api as api
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    resp = exec_client.post("/api/code-execution/format", json={"code": "x=1", "language": "python"})
    assert resp.status_code == 501
    body = resp.get_json()
    assert body["success"] is False
    assert "black" in body["error"] and "pip install black" in body["error"]


def test_format_json_uses_the_standard_library(exec_client):
    resp = exec_client.post("/api/code-execution/format", json={"code": '{"b":1,"a":[1,2]}', "language": "json"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tool"] == "json"
    assert body["formattedCode"] == '{\n  "b": 1,\n  "a": [\n    1,\n    2\n  ]\n}\n'
    assert body["changes"]


def test_format_invalid_json_is_a_422(exec_client):
    resp = exec_client.post("/api/code-execution/format", json={"code": '{"a":}', "language": "json"})
    assert resp.status_code == 422
    assert resp.get_json()["success"] is False


def test_lint_without_flake8_falls_back_to_the_syntax_check_and_says_so(exec_client, monkeypatch):
    import backend.api.code_execution_api as api
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    resp = exec_client.post("/api/code-execution/lint", json={"code": "def f(:\n  pass\n", "language": "python"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tool"] == "python-ast"
    assert "flake8" in body["note"]
    assert body["errors"][0]["line"] == 1
    assert body["score"] == 90


def test_lint_unsupported_language_is_a_501_not_a_clean_report(exec_client, monkeypatch):
    import backend.api.code_execution_api as api
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    resp = exec_client.post("/api/code-execution/lint", json={"code": "SELECT 1", "language": "sql"})
    assert resp.status_code == 501
    assert resp.get_json()["success"] is False


def test_flake8_output_is_parsed_from_stdout():
    from backend.api.code_execution_api import parse_flake8_output
    errors, warnings = parse_flake8_output(
        "/tmp/a.py:1:1: F401 'os' imported but unused\n/tmp/a.py:3:80: W291 trailing whitespace\n"
    )
    assert [e["code"] for e in errors] == ["F401"]
    assert [w["line"] for w in warnings] == [3]


def test_validate_returns_real_syntax_errors(intel_client):
    resp = intel_client.post("/api/code-intelligence/validate", json={"content": "x = (\n", "language": "python"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["mode"] == "syntax"
    assert data["errors"] and data["errors"][0]["line"] in (1, 2)


def test_validate_says_unchecked_for_a_language_it_cannot_parse(intel_client):
    resp = intel_client.post("/api/code-intelligence/validate", json={"content": "x", "language": "ruby"})
    data = resp.get_json()["data"]
    assert data["mode"] == "unchecked"
    assert data["errors"] == []
