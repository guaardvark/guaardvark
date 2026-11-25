"""The safety rules decide() enforces, whatever a model answers.

Guards never ask a model: no file in decision_data.GUARD_MODULES imports the
decision modules, and decide() refuses a call made from one of them.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.services import decision_settings
from backend.services import nl_control_plane as ncp
from backend.services.decision_data import GUARD_MODULES

REPO = Path(ncp._REPO_ROOT)

# Everything that asks a model, or that decides how a model is asked.
DECISION_MODULES = (
    "backend.services.nl_control_plane",
    "backend.services.decision_data",
    "backend.services.decision_prompt",
    "backend.services.decision_evidence",
    "backend.services.decision_settings",
)
DECISION_TAILS = {m.rsplit(".", 1)[-1] for m in DECISION_MODULES}


def _is_decision_module(name: str) -> bool:
    return name in DECISION_MODULES or name.rsplit(".", 1)[-1] in DECISION_TAILS


def _guard_files():
    for path, functions in sorted(GUARD_MODULES.items()):
        if path.endswith("/"):
            for file in sorted((REPO / path).rglob("*.py")):
                yield file, None
        else:
            yield REPO / path, functions


def _forbidden_bindings(tree: ast.AST) -> tuple[list[str], set[str]]:
    """(what a file imports from the decision modules, the names those imports bind)."""
    found, bound = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_decision_module(alias.name):
                    found.append(alias.name)
                    bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if _is_decision_module(module) or _is_decision_module(f"{module}.{alias.name}"):
                    found.append(f"{module}.{alias.name}")
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in DECISION_MODULES:
            found.append(node.value)
    return found, bound


def test_every_guard_path_exists():
    for path, functions in GUARD_MODULES.items():
        target = REPO / path
        assert target.exists(), f"GUARD_MODULES names {path}, which does not exist"
        if functions:
            tree = ast.parse(target.read_text(encoding="utf-8"))
            defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert set(functions) <= defined, (path, set(functions) - defined)


@pytest.mark.parametrize("file, functions", list(_guard_files()), ids=lambda v: str(v))
def test_no_guard_imports_the_decision_modules(file, functions):
    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    if functions is None:
        found, _ = _forbidden_bindings(tree)
        assert not found, f"{file.relative_to(REPO)} imports {found}"
        return
    _, module_bound = _forbidden_bindings(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            found, _ = _forbidden_bindings(node)
            used = sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & module_bound)
            assert not found and not used, f"{node.name} in {file.relative_to(REPO)} reaches {found or used}"


def _call_from(path: str, function: str = "check"):
    """Run decide() from code that the interpreter believes lives in ``path``."""
    source = (
        f"def {function}():\n"
        "    return decide('needs_live_data', {'message': 'latest news?'}, fallback=False)\n"
    )
    namespace = {"decide": ncp.decide}
    exec(compile(source, str(REPO / path), "exec"), namespace)
    return namespace[function]()


@pytest.fixture
def every_decision_on(monkeypatch):
    monkeypatch.setattr(decision_settings, "get_decision_mode", lambda d: "on")


@pytest.fixture
def no_model(monkeypatch):
    """Fails the test if decide() reaches for the model or the GPU state."""
    def refuse(*args, **kwargs):
        raise AssertionError("a guard's call must not reach the model")

    for seam in ("_chat", "_resident", "gpu_busy", "_chat_model"):
        if hasattr(ncp, seam):
            monkeypatch.setattr(ncp, seam, refuse)


@pytest.mark.parametrize("path", [
    "backend/utils/path_guard.py",
    "backend/services/job_operation_gate.py",
    "backend/services/social_outreach/gates.py",
    "scripts/inbound_guard/policy.py",
])
def test_a_call_from_a_guard_is_refused(every_decision_on, no_model, path):
    result = _call_from(path)
    assert (result.answer, result.source, result.reason) == (False, "fallback", "guard_caller")


def test_only_the_named_function_of_a_partial_guard_is_refused(every_decision_on, no_model):
    assert _call_from("backend/tools/llama_code_tools.py", "verify_change").reason == "guard_caller"
    assert ncp.guard_for(str(REPO / "backend/tools/llama_code_tools.py"), "read_file") is None


def test_code_outside_the_repository_is_not_a_guard():
    assert ncp.guard_for("/nowhere/backend/utils/path_guard.py", "check") is None


# ---- a check that fails never reads as done, approved or a match -----------------------------

from backend.services import decision_evidence  # noqa: E402
from backend.services.decision_data import (  # noqa: E402
    BACKEND_POLICY,
    DECISIONS,
    WRITE_LIMIT_KINDS,
    WRITE_LIMITS,
)
from backend.services.nl_control_plane import JsonChatResult  # noqa: E402
from backend.tests.fixtures.decision_sets import load_set  # noqa: E402

MODEL = "gemma4:12b"
BUILD = "4eb23ef187e2" + "c" * 52
L4 = sorted(k for k, v in DECISIONS.items() if v["layer"] == "L4")
L1_L3 = sorted(k for k, v in DECISIONS.items() if v["layer"] in ("L1", "L2", "L3"))


class Seams:
    """A loaded, qualified model whose reply and surroundings each test sets."""

    def __init__(self, monkeypatch):
        self.reply = JsonChatResult(data={"answer": True, "choice": None})
        self.busy = None
        self.resident = {"name": MODEL, "digest": BUILD, "size_vram": 1, "context_length": 8192}
        self.asked = 0
        monkeypatch.setattr(decision_settings, "get_decision_mode", lambda d: "on")
        monkeypatch.setattr(decision_evidence, "qualifies", lambda decision, digest, backend="chat": True)
        monkeypatch.setattr(ncp, "gpu_busy", lambda: self.busy)
        monkeypatch.setattr(ncp, "_chat_model", lambda ctx: MODEL)
        monkeypatch.setattr(ncp, "_resident", lambda model: self.resident)
        monkeypatch.setattr(ncp, "_chat", self._chat)

    def _chat(self, system, user, *, model, timeout, num_ctx):
        self.asked += 1
        return self.reply


@pytest.fixture
def seams(monkeypatch):
    # Shadow questions are held, not run on the shadow worker after the test.
    monkeypatch.setattr(ncp, "_submit_shadow", lambda job: None)
    return Seams(monkeypatch)


def _state(entry, oversize=False):
    return {field: "x" * (limit + 1 if oversize else 1) for field, (_src, limit) in entry["state"].items()}


def _run_kind(entry):
    """A run kind the decision's layer allows: batch for L5, interactive otherwise."""
    return "interactive" if "interactive" in BACKEND_POLICY[entry["layer"]]["runs"] else "batch"


FAILURES = {
    "gpu_busy": lambda s, entry: (setattr(s, "busy", "video_render is running"), _state(entry), None),
    "not_resident": lambda s, entry: (setattr(s, "resident", None), _state(entry), None),
    "timeout": lambda s, entry: (setattr(s, "reply", JsonChatResult(failure="timeout")), _state(entry), None),
    "error": lambda s, entry: (setattr(s, "reply", JsonChatResult(failure="error")), _state(entry), None),
    "parse_fail": lambda s, entry: (setattr(s, "reply", JsonChatResult(data={"answer": "maybe"})), _state(entry), None),
    "too_long": lambda s, entry: (None, _state(entry, oversize=True), None),
    "bad_state": lambda s, entry: (None, {"unexpected": "field"}, None),
    "author_is_judge": lambda s, entry: (None, _state(entry), [MODEL]),
}


@pytest.mark.parametrize("reason", sorted(FAILURES))
@pytest.mark.parametrize("decision_id", L4)
def test_a_failed_check_is_not_verified(seams, decision_id, reason):
    entry = DECISIONS[decision_id]
    assert entry["kind"] == "bool" and entry["safe_side"] is False
    _, state, authors = FAILURES[reason](seams, entry)
    result = ncp.decide(decision_id, state, fallback=True, exclude_models=authors)
    assert (result.answer, result.source, result.reason) == (False, "safe_side", reason)


@pytest.mark.parametrize("reason", sorted(FAILURES))
@pytest.mark.parametrize("decision_id", L1_L3)
def test_a_failed_question_elsewhere_returns_the_fallback(seams, decision_id, reason):
    entry = DECISIONS[decision_id]
    _, state, authors = FAILURES[reason](seams, entry)
    if authors is None and entry["layer"] == "L3":
        authors = ()
    result = ncp.decide(decision_id, state, fallback="today", exclude_models=authors)
    assert (result.answer, result.source, result.reason) == ("today", "fallback", reason)


@pytest.mark.parametrize("decision_id", L4)
def test_a_check_in_shadow_or_below_its_floor_keeps_todays_answer(seams, monkeypatch, decision_id):
    entry = DECISIONS[decision_id]
    monkeypatch.setattr(decision_evidence, "qualifies", lambda decision, digest, backend="chat": False)
    assert ncp.decide(decision_id, _state(entry), fallback=True).answer is True
    monkeypatch.setattr(decision_settings, "get_decision_mode", lambda d: "shadow")
    assert ncp.decide(decision_id, _state(entry), fallback=True).answer is True


def test_an_error_inside_a_check_that_is_on_is_not_verified(seams, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(ncp, "_decide_live", broken)
    result = ncp.decide("task_done", _state(DECISIONS["task_done"]), fallback=True)
    assert (result.answer, result.source, result.reason) == (False, "safe_side", "error")


# ---- third-party text may only lead to a veto ---------------------------------------------------

def _injection_items(decision_id):
    return [item for item in load_set(decision_id)["items"] if "injection" in item["tags"]]


@pytest.mark.parametrize("decision_id, unlocking, veto", [
    ("context_answers", {"answer": True}, {"answer": False}),
    ("outreach_thread_fit", {"choice": "good_fit"}, {"choice": "skip"}),
])
def test_third_party_text_cannot_unlock(seams, decision_id, unlocking, veto):
    entry = DECISIONS[decision_id]
    items = _injection_items(decision_id)
    assert items, f"{decision_id} carries no injection items"
    for item in items:
        seams.reply = JsonChatResult(data=unlocking)
        result = ncp.decide(decision_id, item["state"], fallback=entry["safe_side"], exclude_models=())
        assert (result.answer, result.reason) == (entry["safe_side"], "third_party_unlock"), item["id"]
        seams.reply = JsonChatResult(data=veto)
        result = ncp.decide(decision_id, item["state"], fallback=entry["safe_side"], exclude_models=())
        assert (result.answer, result.source) == (entry["safe_side"], "llm"), item["id"]


def test_user_typed_text_may_unlock_but_decide_runs_nothing(seams, monkeypatch):
    """needs_live_data reads only the user's own message, so a True stands. It
    only changes a nudge: any search still goes through the model's own tool
    call and the web-access setting, and decide() itself reaches nothing."""
    import requests
    import socket

    reached = []
    for verb in ("get", "post", "request"):
        monkeypatch.setattr(requests, verb, lambda *a, **k: reached.append("requests"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: reached.append("socket"))
    for item in _injection_items("needs_live_data"):
        seams.reply = JsonChatResult(data={"answer": True})
        result = ncp.decide("needs_live_data", item["state"], fallback=False)
        assert (result.answer, result.reason) == (True, "ok"), item["id"]
    assert reached == []


@pytest.mark.parametrize("decision_id", sorted(k for k, v in DECISIONS.items()
                                               if any(s == "third_party" for s, _ in v["state"].values())))
def test_no_unlocking_answer_survives_third_party_state(seams, decision_id):
    entry = DECISIONS[decision_id]
    for answer in entry["unlocks"]:
        key = "answer" if entry["kind"] == "bool" else "choice"
        seams.reply = JsonChatResult(data={key: answer})
        result = ncp.decide(decision_id, _state(entry), fallback=entry["safe_side"],
                            run=_run_kind(entry), exclude_models=())
        assert result.answer == entry["safe_side"], (decision_id, answer, result)
        assert result.source != "llm"


# ---- file and database writes stay on paths a person can undo ---------------------------------

def test_every_writing_decision_declares_its_write_limit():
    writers = {k for k, v in DECISIONS.items() if v["side_effect"] == "file_db_write"}
    assert set(WRITE_LIMITS) == writers
    assert set(WRITE_LIMITS.values()) <= set(WRITE_LIMIT_KINDS)
    assert WRITE_LIMITS["fix_addresses_failure"] == "label_only"
    assert WRITE_LIMITS["memory_fact"] == WRITE_LIMITS["memory_relation"] == "reversible_row"
    assert WRITE_LIMITS["chunk_strategy"] == "metadata_only"


DECISION_PATH = ("backend/services/nl_control_plane.py", "backend/services/decision_prompt.py",
                 "backend/services/decision_evidence.py")
WRITE_MODULES = ("backend.tools", "backend.api", "subprocess", "shutil")
WRITE_CALLS = {"write_text", "write_bytes", "unlink", "rmtree", "rename", "makedirs", "mkdir"}


@pytest.mark.parametrize("path", DECISION_PATH)
def test_the_decision_path_writes_nothing(path):
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                assert not name.startswith(WRITE_MODULES), f"{path} imports {name}"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                assert func.attr not in WRITE_CALLS, f"{path} calls {func.attr}"
                chain = ast.unparse(func)
                assert not chain.endswith(("session.add", "session.commit", "session.delete")), f"{path}: {chain}"
            if isinstance(func, ast.Name) and func.id == "open":
                modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
                modes += [k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
                assert not any(set(str(m)) & set("wax+") for m in modes), f"{path} opens a file for writing"


@pytest.fixture
def app():
    from flask import Flask

    from backend.models import db

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_a_yes_on_a_proposed_fix_leaves_it_for_a_person_to_apply(app, seams):
    from backend.models import PendingFix, db

    fix = PendingFix(file_path="backend/x.py", proposed_diff="--- a\n+++ b\n", status="proposed")
    db.session.add(fix)
    db.session.commit()
    seams.reply = JsonChatResult(data={"answer": True})
    result = ncp.decide("fix_addresses_failure", {"failure": "AssertionError in test_x", "diff": "--- a\n+++ b\n"},
                        fallback=False)
    assert (result.answer, result.reason) == (True, "ok")
    db.session.refresh(fix)
    assert fix.status == "proposed" and fix.applied_at is None


def test_contradicting_memories_are_both_kept(app, seams):
    import uuid

    from backend.models import AgentMemory, db

    rows = [AgentMemory(id=str(uuid.uuid4()), content=text) for text in ("I live in Leeds", "I never lived in Leeds")]
    db.session.add_all(rows)
    db.session.commit()
    seams.reply = JsonChatResult(data={"choice": "contradicts"})
    result = ncp.decide("memory_relation", {"stored_fact": rows[0].content, "new_fact": rows[1].content},
                        fallback="unrelated", run="batch", exclude_models=())
    assert result.answer == "contradicts"
    assert AgentMemory.query.count() == 2
