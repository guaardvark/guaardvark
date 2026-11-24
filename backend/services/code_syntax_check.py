"""Real, dependency-free syntax checks for the Code Editor.

The editor's Debug button and the code-intelligence /validate route used to call
tools that are not part of the install (flake8, eslint) or a model whose answer
was thrown away, and then report "no problems". This module is what both can
truthfully do on a stock machine: Python and JSON through the standard library,
JavaScript, JSX and TypeScript through the web app's own esbuild when it is
installed, plain JavaScript through `node --check` otherwise. Anything else is
reported as unsupported rather than as clean.

`check_file_syntax` picks the language from a file name and is the one check
the edit paths share, so "syntax verified" means the same thing everywhere.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# Languages the checker can actually parse, and the tool it uses for each.
SUPPORTED = {
    "python": "python-ast",
    "json": "json",
    "javascript": "node --check",
}

_NODE_LOCATION = re.compile(r":(\d+)(?::(\d+))?\s*$")
_ESBUILD_ERROR = re.compile(r"\[ERROR\]\s*(.*)")
_ESBUILD_LOCATION = re.compile(r"<stdin>:(\d+):(\d+):")
# `node --check` reads a .js file as CommonJS and lets a broken ES module
# through (node 22: `import a from "a"; const b = = 1;` exits 0), so a file
# with import/export statements is checked as .mjs.
_ES_MODULE = re.compile(r"^\s*(?:import\b|export\b)", re.MULTILINE)

# File suffix -> (language, esbuild loader). esbuild's jsx loader also reads
# plain JavaScript, which is what most .js files in a React app are.
_BY_SUFFIX = {
    ".py": ("python", None),
    ".json": ("json", None),
    ".js": ("javascript", "jsx"),
    ".mjs": ("javascript", "js"),
    ".cjs": ("javascript", "js"),
    ".jsx": ("javascript", "jsx"),
    ".ts": ("typescript", "ts"),
    ".tsx": ("typescript", "tsx"),
}


def esbuild_path():
    """The esbuild that ships with the web app's dependencies, or None."""
    candidate = Path(__file__).resolve().parents[2] / "frontend" / "node_modules" / ".bin" / "esbuild"
    return str(candidate) if candidate.exists() else None


def _check_with_esbuild(code, loader, binary):
    """(errors, ran). `ran` is False when esbuild itself could not run, which
    is "not checked", not a syntax error."""
    try:
        result = subprocess.run(
            [binary, f"--loader={loader}", "--log-level=error", "--color=false"],
            input=code, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], False
    if result.returncode == 0:
        return [], True
    text = result.stderr or result.stdout or ""
    # esbuild prints `✘ [ERROR] <message>`, then `<stdin>:<line>:<column>:`
    # with a zero-based column.
    message = _ESBUILD_ERROR.search(text)
    where = _ESBUILD_LOCATION.search(text)
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "syntax error")
    return [_issue(
        where.group(1) if where else 1,
        int(where.group(2)) + 1 if where else 1,
        (message.group(1).strip() if message else first)[:400],
    )], True


def _issue(line, column, message, code="syntax"):
    return {
        "line": int(line) if line else 1,
        "column": int(column) if column else 1,
        "code": code,
        "message": message,
        "severity": 2,
    }


def _check_python(code):
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return [_issue(exc.lineno, exc.offset, exc.msg or "invalid syntax")]
    return []


def _check_json(code):
    try:
        json.loads(code)
    except json.JSONDecodeError as exc:
        return [_issue(exc.lineno, exc.colno, exc.msg)]
    return []


def _check_javascript(code, node_path):
    suffix = ".mjs" if _ES_MODULE.search(code) else ".cjs"
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8")
    try:
        tmp.write(code)
        tmp.close()
        result = subprocess.run(
            [node_path, "--check", tmp.name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    if result.returncode == 0:
        return []
    return [parse_node_check_error(result.stderr, tmp.name)]


def parse_node_check_error(stderr, file_name=""):
    """Turn `node --check` stderr into one issue with the line it points at.

    node prints `<file>:<line>` first, then the offending source line, a caret
    line and `SyntaxError: <message>`.
    """
    lines = [ln for ln in (stderr or "").splitlines() if ln.strip()]
    line_no = 1
    message = "SyntaxError"
    for raw in lines:
        if file_name and raw.startswith(file_name):
            match = _NODE_LOCATION.search(raw)
            if match:
                line_no = int(match.group(1))
        elif raw.startswith("SyntaxError"):
            message = raw.strip()
    column = 1
    caret = next((ln for ln in lines if set(ln.strip()) == {"^"}), None)
    if caret is not None:
        column = caret.index("^") + 1
    return _issue(line_no, column, message)


def check_syntax(code, language):
    """Check `code` and say which tool did it.

    Returns {"supported", "tool", "errors", "warnings", "note"}. `supported` is
    False when nothing on this machine can parse the language; callers must then
    report "not checked", never "no problems".
    """
    lang = (language or "").lower()
    if lang == "python":
        return {"supported": True, "tool": SUPPORTED[lang], "errors": _check_python(code), "warnings": [], "note": ""}
    if lang == "json":
        return {"supported": True, "tool": SUPPORTED[lang], "errors": _check_json(code), "warnings": [], "note": ""}
    if lang in ("javascript", "jsx", "typescript", "tsx"):
        binary = esbuild_path()
        if binary:
            loader = {"javascript": "jsx", "jsx": "jsx", "typescript": "ts", "tsx": "tsx"}[lang]
            errors, ran = _check_with_esbuild(code, loader, binary)
            if ran:
                return {"supported": True, "tool": "esbuild", "errors": errors, "warnings": [], "note": ""}
    if lang == "javascript":
        node_path = shutil.which("node")
        if not node_path:
            return {
                "supported": False,
                "tool": None,
                "errors": [],
                "warnings": [],
                "note": "JavaScript syntax check needs node on PATH; it is not installed here.",
            }
        return {"supported": True, "tool": SUPPORTED[lang], "errors": _check_javascript(code, node_path), "warnings": [], "note": ""}
    return {
        "supported": False,
        "tool": None,
        "errors": [],
        "warnings": [],
        "note": f"No syntax checker for {language or 'this language'} on this machine.",
    }


def check_file_syntax(file_name, code):
    """`check_syntax` for a file, the language taken from its name.

    Same result shape. A file type nothing here can parse comes back
    `supported: False`; callers report that as "not checked".
    """
    suffix = Path(str(file_name)).suffix.lower()
    language, loader = _BY_SUFFIX.get(suffix, (None, None))
    if language is None:
        return {"supported": False, "tool": None, "errors": [], "warnings": [],
                "note": f"No syntax checker for {suffix or 'this file type'} files."}
    if loader:
        binary = esbuild_path()
        if binary:
            errors, ran = _check_with_esbuild(code, loader, binary)
            if ran:
                return {"supported": True, "tool": "esbuild", "errors": errors, "warnings": [], "note": ""}
        if suffix in (".jsx", ".ts", ".tsx"):
            return {"supported": False, "tool": None, "errors": [], "warnings": [],
                    "note": f"{suffix} files are checked with the web app's esbuild, which is not installed here."}
    return check_syntax(code, language)
