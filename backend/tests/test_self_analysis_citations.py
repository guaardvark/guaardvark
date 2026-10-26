"""Questions about Guaardvark's own code: which turns count, and how the
reply's file references are checked and remembered.

A small fake checkout under tmp_path stands in for the install; the System
Mapper is not built here.
"""
import pytest

from backend.services import self_analysis as sa


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "backend" / "services").mkdir(parents=True)
    (root / "backend" / "services" / "engine.py").write_text("".join(f"line {i}\n" for i in range(1, 41)))
    monkeypatch.setattr(sa, "_root", lambda: root.resolve())
    import backend.config as config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path / "data"), raising=False)
    return root


@pytest.mark.parametrize("text", [
    "Where in your code are slash commands handled?",
    "Which of your files decides the context window?",
    "How do you pick a model for image generation?",
    "Where does plan mode take over a chat turn?",
    "Look at backend/services/engine.py and explain it",
])
def test_questions_about_guaardvark_count(text):
    assert sa.about_self(text)


@pytest.mark.parametrize("text", ["Make a picture of a cat", "Thanks!", "What is the capital of Peru?",
                                  "How do you say hello in French?", "How do you make a cake?"])
def test_other_turns_do_not(text):
    assert not sa.about_self(text)


def test_references_are_checked_against_the_checkout(checkout):
    reply = ("It lives in backend/services/engine.py:12, see also backend/services/engine.py:12-20, "
             "backend/services/engine.py:400 and backend/services/missing.py:3.")
    report = sa.check_citations(reply)
    assert [v["ref"] for v in report["valid"]] == ["backend/services/engine.py:12", "backend/services/engine.py:12-20"]
    assert {i["ref"] for i in report["invalid"]} == {"backend/services/engine.py:400", "backend/services/missing.py:3"}
    assert "missing.py:3" in sa.invalid_note(report)


def test_an_answer_with_sound_references_is_remembered_until_the_file_changes(checkout):
    report = sa.check_citations("See backend/services/engine.py:5.")
    assert sa.remember("where is the engine?", "In backend/services/engine.py:5.", report) == 1
    notes = sa.notes_for(["backend/services/engine.py"])
    assert notes and "where is the engine?" in notes[0]
    (checkout / "backend" / "services" / "engine.py").write_text("changed\n")
    assert sa.notes_for(["backend/services/engine.py"]) == []


def test_an_answer_with_a_bad_reference_is_not_remembered(checkout):
    report = sa.check_citations("See backend/services/engine.py:5 and backend/nope.py.")
    assert sa.remember("q", "a", report) == 0
