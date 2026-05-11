"""
Tests for Phase 4 + Phase 5 of the see-think-act-remember loop
(plans/2026-05-10-see-think-act-remember-loop-design.md).

Phase 4 — session belief tracker + lesson generation:
  - Expectation dataclass shape
  - _derive_session_expectations() parses self_knowledge_compact.md
    and recipes.json into structured Expectation rows with source provenance
  - _record_expectation_contradictions() compares expectations against
    a WORLD_OBSERVED block, appends contradictions to _expectation_log
  - _distill_lessons() dedups by element name (case-insensitive) and caps at 5
  - End-of-task path writes belief_update memories via the memory_api helper
  - memory_api.add_memory() is now an in-process callable; the existing POST
    route is a thin wrapper around it

Phase 5 — cross-session reconciliation:
  - lesson_reconciler.scan_belief_updates() groups by element name
  - when count >= 3 it creates a PendingFix row with a unified diff
  - source provenance "model_belief" rows are ignored (no file to edit)
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

# Flask-dependent tests are gated behind import availability so the pure
# dataclass / parser tests still run without Flask + SQLAlchemy installed.
try:
    from flask import Flask
    from backend.models import db, AgentMemory, PendingFix
    _FLASK_AVAILABLE = True
except Exception:
    _FLASK_AVAILABLE = False


# ----------------------------------------------------------------------------
# Phase 4 — Expectation dataclass
# ----------------------------------------------------------------------------

class TestExpectationDataclass(unittest.TestCase):
    """Expectation rows carry enough provenance for Phase 5 to act on them."""

    def test_expectation_has_required_fields(self):
        from backend.services.agent_control_service import Expectation
        exp = Expectation(
            element="Firefox flame icon",
            expected_visible=True,
            observed_visible=False,
            source="self_knowledge_compact.md",
            source_line=42,
            confidence=0.6,
        )
        self.assertEqual(exp.element, "Firefox flame icon")
        self.assertTrue(exp.expected_visible)
        self.assertFalse(exp.observed_visible)
        self.assertEqual(exp.source, "self_knowledge_compact.md")
        self.assertEqual(exp.source_line, 42)
        self.assertAlmostEqual(exp.confidence, 0.6)

    def test_expectation_defaults_reasonable(self):
        from backend.services.agent_control_service import Expectation
        exp = Expectation(element="X")
        # Sensible defaults so callers can fill incrementally.
        self.assertTrue(exp.expected_visible)
        self.assertFalse(exp.observed_visible)
        self.assertEqual(exp.source, "")
        self.assertIsNone(exp.source_line)


# ----------------------------------------------------------------------------
# Phase 4 — _derive_session_expectations
# ----------------------------------------------------------------------------

class TestDeriveSessionExpectations(unittest.TestCase):
    """Parses agent knowledge files into structured expectations with source refs."""

    def setUp(self):
        import backend.services.agent_control_service as acs
        self.svc = acs.AgentControlService()

    def test_derives_desktop_icons_from_self_knowledge(self):
        # self_knowledge_compact.md lists desktop icons (Trash, Home, Firefox …)
        # in a bullet block. Each one becomes an Expectation with the source
        # file and the line number where the bullet lives.
        exps = self.svc._derive_session_expectations()
        elements = {e.element.lower() for e in exps}
        # A few we know are in the file.
        self.assertIn("trash", elements)
        self.assertIn("home", elements)
        self.assertTrue(
            any("firefox" in e for e in elements),
            f"expected a firefox icon expectation, got {elements}",
        )

    def test_source_is_self_knowledge_file_with_line(self):
        exps = self.svc._derive_session_expectations()
        firefox_exps = [e for e in exps if "firefox" in e.element.lower()]
        self.assertTrue(firefox_exps, "no firefox expectation derived")
        for e in firefox_exps:
            self.assertEqual(e.source, "self_knowledge_compact.md")
            # Source line points into the bullet block; positive int.
            self.assertIsInstance(e.source_line, int)
            self.assertGreater(e.source_line, 0)

    def test_expectations_dedup_by_lowercase_element(self):
        # Same element name listed twice (or in two files) collapses to one row.
        exps = self.svc._derive_session_expectations()
        names = [e.element.lower() for e in exps]
        self.assertEqual(len(names), len(set(names)),
                         "expectations contain duplicate element names")


# ----------------------------------------------------------------------------
# Phase 4 — _record_expectation_contradictions
# ----------------------------------------------------------------------------

class TestRecordExpectationContradictions(unittest.TestCase):
    """When an expectation says X is visible but WORLD_OBSERVED doesn't list X,
    that's a contradiction worth remembering."""

    def setUp(self):
        import backend.services.agent_control_service as acs
        self.svc = acs.AgentControlService()

    def test_appends_to_expectation_log_on_contradiction(self):
        from backend.services.agent_control_service import Expectation
        expectations = [
            Expectation(element="Firefox flame icon", expected_visible=True,
                        source="self_knowledge_compact.md", source_line=50),
            Expectation(element="Trash", expected_visible=True,
                        source="self_knowledge_compact.md", source_line=42),
        ]
        # WORLD_OBSERVED says we see a search bar and a settings cog —
        # neither Firefox flame nor Trash is here.
        world_observed = (
            "WORLD_OBSERVED (fresh capture, no task bias, no priming):\n"
            "- search bar at top of page\n"
            "- settings cog button\n"
            "- new tab button\n"
        )
        self.svc._record_expectation_contradictions(expectations, world_observed)
        self.assertEqual(len(self.svc._expectation_log), 2)
        elements = {e.element for e in self.svc._expectation_log}
        self.assertEqual(elements, {"Firefox flame icon", "Trash"})
        for e in self.svc._expectation_log:
            self.assertTrue(e.expected_visible)
            self.assertFalse(e.observed_visible)

    def test_no_contradiction_when_element_in_world_observed(self):
        from backend.services.agent_control_service import Expectation
        expectations = [
            Expectation(element="Firefox flame icon", expected_visible=True,
                        source="self_knowledge_compact.md", source_line=50),
        ]
        # Element actually appears in observation — no contradiction.
        world_observed = (
            "WORLD_OBSERVED:\n"
            "- Firefox flame icon\n"
            "- desktop\n"
        )
        self.svc._record_expectation_contradictions(expectations, world_observed)
        self.assertEqual(self.svc._expectation_log, [])

    def test_substring_match_is_case_insensitive(self):
        # The vision model may say "firefox" while the doc says "Firefox flame icon".
        # Partial token overlap counts as observed.
        from backend.services.agent_control_service import Expectation
        expectations = [
            Expectation(element="Firefox flame icon", expected_visible=True,
                        source="self_knowledge_compact.md", source_line=50),
        ]
        world_observed = "WORLD_OBSERVED:\n- firefox window in focus\n- url bar\n"
        self.svc._record_expectation_contradictions(expectations, world_observed)
        self.assertEqual(self.svc._expectation_log, [])

    def test_empty_world_observed_skips_recording(self):
        # If re-grounding failed, _observe_only_pass returns "". No signal,
        # no contradictions — we don't want to record false positives.
        from backend.services.agent_control_service import Expectation
        expectations = [
            Expectation(element="Firefox flame icon", expected_visible=True,
                        source="self_knowledge_compact.md", source_line=50),
        ]
        self.svc._record_expectation_contradictions(expectations, "")
        self.assertEqual(self.svc._expectation_log, [])

    def test_records_model_belief_when_stuck_target_unobserved(self):
        # The model named a target ("orange unicorn button") and got stuck on it.
        # It's not in self_knowledge, but the stuck loop is still useful evidence.
        # Source="model_belief" so Phase 5 ignores it for file edits but the
        # next session prompt still carries the lesson.
        self.svc._stuck_target = "orange unicorn button"
        self.svc._stuck_target_count = 2
        world_observed = "WORLD_OBSERVED:\n- search bar\n- settings\n"
        self.svc._record_expectation_contradictions([], world_observed)
        self.assertEqual(len(self.svc._expectation_log), 1)
        row = self.svc._expectation_log[0]
        self.assertEqual(row.element, "orange unicorn button")
        self.assertEqual(row.source, "model_belief")
        self.assertFalse(row.observed_visible)


# ----------------------------------------------------------------------------
# Phase 4 — _distill_lessons
# ----------------------------------------------------------------------------

class TestDistillLessons(unittest.TestCase):
    """End-of-task collapse: dedup by element name, cap at 5 lessons."""

    def setUp(self):
        import backend.services.agent_control_service as acs
        self.svc = acs.AgentControlService()

    def test_distills_one_lesson_per_unique_element(self):
        from backend.services.agent_control_service import Expectation
        self.svc._expectation_log = [
            Expectation(element="Firefox flame", expected_visible=True,
                        observed_visible=False, source="self_knowledge_compact.md",
                        source_line=50),
            Expectation(element="Firefox flame", expected_visible=True,
                        observed_visible=False, source="self_knowledge_compact.md",
                        source_line=50),  # duplicate within session
            Expectation(element="Trash icon", expected_visible=True,
                        observed_visible=False, source="self_knowledge_compact.md",
                        source_line=42),
        ]
        lessons = self.svc._distill_lessons()
        self.assertEqual(len(lessons), 2)
        # Each lesson is a dict with at least content + element + source.
        elements = {l["element"] for l in lessons}
        self.assertEqual(elements, {"Firefox flame", "Trash icon"})

    def test_distill_caps_at_five_lessons(self):
        from backend.services.agent_control_service import Expectation
        self.svc._expectation_log = [
            Expectation(element=f"element-{i}", expected_visible=True,
                        observed_visible=False, source="self_knowledge_compact.md",
                        source_line=10 + i)
            for i in range(12)
        ]
        lessons = self.svc._distill_lessons()
        self.assertEqual(len(lessons), 5)

    def test_distill_ignores_non_contradictions(self):
        from backend.services.agent_control_service import Expectation
        self.svc._expectation_log = [
            Expectation(element="Visible thing", expected_visible=True,
                        observed_visible=True, source="self_knowledge_compact.md",
                        source_line=50),
        ]
        self.assertEqual(self.svc._distill_lessons(), [])

    def test_lesson_content_is_actionable(self):
        from backend.services.agent_control_service import Expectation
        self.svc._expectation_log = [
            Expectation(element="Shortcuts panel", expected_visible=True,
                        observed_visible=False, source="self_knowledge_compact.md",
                        source_line=32),
        ]
        lessons = self.svc._distill_lessons()
        self.assertEqual(len(lessons), 1)
        content = lessons[0]["content"]
        self.assertIn("Shortcuts panel", content)
        # The lesson must hint at the contradiction, not just name the element.
        self.assertTrue(
            "not visible" in content.lower() or "not observed" in content.lower(),
            f"lesson content not actionable: {content!r}",
        )


# ----------------------------------------------------------------------------
# Phase 4 — memory_api.add_memory() helper
# ----------------------------------------------------------------------------

@pytest.mark.skipif(not _FLASK_AVAILABLE, reason="Flask not available")
class TestAddMemoryHelper:
    """memory_api exposes add_memory() as an in-process callable that the POST
    route wraps. Agent service uses this to write belief_update memories without
    going through HTTP."""

    @pytest.fixture
    def app(self):
        from flask import Flask
        from backend.models import db
        from backend.api.memory_api import memory_bp
        app = Flask(__name__)
        app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
        db.init_app(app)
        app.register_blueprint(memory_bp)
        with app.app_context():
            db.create_all()
            yield app
            db.session.remove()
            db.drop_all()

    def test_add_memory_creates_row_with_type(self, app):
        from backend.api.memory_api import add_memory
        from backend.models import db, AgentMemory
        with app.app_context():
            mem = add_memory(
                content="Shortcuts panel was not visible this session",
                memory_type="belief_update",
                source="agent",
                importance=0.6,
                tags=["belief_update", "shortcuts panel"],
            )
            assert mem is not None
            row = db.session.query(AgentMemory).filter_by(id=mem.id).first()
            assert row is not None
            assert row.type == "belief_update"
            assert row.source == "agent"
            assert "Shortcuts panel" in row.content

    def test_add_memory_returns_none_on_blank_content(self, app):
        from backend.api.memory_api import add_memory
        with app.app_context():
            assert add_memory(content="   ", memory_type="belief_update") is None

    def test_existing_post_route_still_works(self, app):
        # Backward-compat: the HTTP route shouldn't regress.
        client = app.test_client()
        resp = client.post("/api/memory", json={
            "content": "via HTTP", "type": "note",
        })
        assert resp.status_code == 201
        assert resp.get_json()["memory"]["type"] == "note"


# ----------------------------------------------------------------------------
# Phase 5 — lesson_reconciler
# ----------------------------------------------------------------------------

@pytest.mark.skipif(not _FLASK_AVAILABLE, reason="Flask not available")
class TestLessonReconciler:
    """Scans belief_update memories, proposes a PendingFix when >=3 sessions
    agree the same element wasn't visible."""

    @pytest.fixture
    def app(self):
        from flask import Flask
        from backend.models import db
        from backend.api.memory_api import memory_bp
        app = Flask(__name__)
        app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
        db.init_app(app)
        app.register_blueprint(memory_bp)
        with app.app_context():
            db.create_all()
            yield app
            db.session.remove()
            db.drop_all()

    def _seed(self, app, element, count, source="self_knowledge_compact.md", source_line=50):
        from backend.api.memory_api import add_memory
        with app.app_context():
            for i in range(count):
                add_memory(
                    content=f"{element} was not visible during session {i}",
                    memory_type="belief_update",
                    source="agent",
                    tags=["belief_update", element.lower(),
                          f"src:{source}:{source_line}"],
                )

    def test_creates_pending_fix_when_three_agree(self, app):
        from backend.services.lesson_reconciler import scan_belief_updates
        from backend.models import db, PendingFix
        self._seed(app, "Shortcuts panel", count=3)
        with app.app_context():
            created = scan_belief_updates()
            assert created == 1
            pf = db.session.query(PendingFix).first()
            assert pf is not None
            assert "shortcuts panel" in pf.fix_description.lower()
            assert pf.file_path.endswith("self_knowledge_compact.md")
            # Diff should reference the file in unified-diff format.
            assert "---" in pf.proposed_diff
            assert "+++" in pf.proposed_diff
            assert pf.status == "proposed"

    def test_no_pending_fix_below_threshold(self, app):
        from backend.services.lesson_reconciler import scan_belief_updates
        from backend.models import db, PendingFix
        self._seed(app, "Shortcuts panel", count=2)
        with app.app_context():
            created = scan_belief_updates()
            assert created == 0
            assert db.session.query(PendingFix).count() == 0

    def test_ignores_model_belief_source(self, app):
        # source="model_belief" means the element isn't in any knowledge file —
        # no file to propose an edit against, so the reconciler must skip it.
        from backend.services.lesson_reconciler import scan_belief_updates
        from backend.models import db, PendingFix
        self._seed(app, "Purple unicorn", count=5, source="model_belief", source_line=0)
        with app.app_context():
            assert scan_belief_updates() == 0
            assert db.session.query(PendingFix).count() == 0

    def test_idempotent_run(self, app):
        # Running the reconciler twice with the same evidence must not create
        # a second PendingFix for the same element.
        from backend.services.lesson_reconciler import scan_belief_updates
        from backend.models import db, PendingFix
        self._seed(app, "Shortcuts panel", count=3)
        with app.app_context():
            scan_belief_updates()
            scan_belief_updates()
            assert db.session.query(PendingFix).count() == 1


if __name__ == "__main__":
    unittest.main()
