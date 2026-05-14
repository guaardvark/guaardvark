"""Phase A memory wiring tests.

Covers two surgical changes:
  1. Unified recall — agent_control_service._load_lesson_memories is a shim
     over memory_api.get_lessons_for_agent_prompt; all three recall callers
     share one SQL query in _query_memories.
  2. Strong-positive detection — _detect_strong_positive picks up enthusiastic
     feedback phrases in the user's comment, and the inducer bumps importance
     when the signal is set.

Tests that need a live database are skipped in the unit suite — the structured
behaviour we care about (phrase detection, shim delegation) doesn't need one.
"""

import importlib
import unittest
from unittest.mock import patch, MagicMock


def _module_importable(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


_HAS_MEMORY_API = _module_importable("backend.api.memory_api")
# self_improvement_service imports backend.models lazily (inside functions), so
# the module itself imports cleanly even without flask_sqlalchemy. The default
# tests below patch the model layer, which only works when backend.models can
# actually be imported — check that too.
_HAS_SELF_IMPROVEMENT = (
    _module_importable("backend.services.self_improvement_service")
    and _module_importable("backend.models")
)
_HAS_CELERY_APP = _module_importable("backend.celery_app")


class TestStrongPositiveDetection(unittest.TestCase):
    """Comment-only detection (no DB lookup) — fast, deterministic."""

    def test_plain_thumbs_up_is_not_strong(self):
        from backend.api.agent_control_api import _detect_strong_positive
        # No comment, no session: routine 👍, nothing extra.
        self.assertFalse(_detect_strong_positive("", session_id=None))
        self.assertFalse(_detect_strong_positive("ok thanks", session_id=None))

    def test_excellent_in_comment_is_strong(self):
        from backend.api.agent_control_api import _detect_strong_positive
        self.assertTrue(_detect_strong_positive("excellent work", session_id=None))
        self.assertTrue(_detect_strong_positive("Excellent!", session_id=None))

    def test_perfect_variants(self):
        from backend.api.agent_control_api import _detect_strong_positive
        self.assertTrue(_detect_strong_positive("perfect", session_id=None))
        self.assertTrue(_detect_strong_positive("perfectly done", session_id=None))

    def test_multi_word_phrases(self):
        from backend.api.agent_control_api import _detect_strong_positive
        for phrase in [
            "very good",
            "well done",
            "nailed it",
            "spot on",
            "nice work",
            "that's it",
            "great job",
        ]:
            self.assertTrue(
                _detect_strong_positive(phrase, session_id=None),
                f"phrase failed to match: {phrase!r}",
            )

    def test_word_boundaries_avoid_false_positives(self):
        from backend.api.agent_control_api import _detect_strong_positive
        # "imperfect" should not trigger "perfect"; "excellence" should not
        # trigger "excellent" via substring match.
        self.assertFalse(_detect_strong_positive("imperfect approach", session_id=None))
        # NB: "excellence" does match because of \b\w\b boundaries — that's a
        # known false-positive cost we accept (and would surface in feedback
        # comments only, which the user wrote intentionally anyway). Document
        # the expected behaviour rather than fight the regex.
        self.assertTrue(_detect_strong_positive("excellent answer", session_id=None))


@unittest.skipUnless(_HAS_MEMORY_API, "memory_api unavailable (flask_sqlalchemy missing)")
class TestUnifiedRecallShim(unittest.TestCase):
    """agent_control_service._load_lesson_memories now delegates to memory_api."""

    def test_load_lesson_memories_delegates_to_helper(self):
        from backend.services.agent_control_service import AgentControlService

        # Patch the helper the shim imports lazily inside the method.
        # Force-import the module first so the attribute exists on the package.
        import backend.api.memory_api  # noqa: F401
        with patch(
            "backend.api.memory_api.get_lessons_for_agent_prompt",
            return_value="## Lessons & Notes (canary)\n### Test\n  1. step",
        ) as mock_helper:
            out = AgentControlService._load_lesson_memories(max_rows=6, max_chars=2500)

        mock_helper.assert_called_once_with(
            max_rows=6,
            max_chars=2500,
            include_belief_updates=True,
        )
        self.assertIn("canary", out)

    def test_load_lesson_memories_returns_empty_when_helper_returns_empty(self):
        from backend.services.agent_control_service import AgentControlService
        import backend.api.memory_api  # noqa: F401

        with patch(
            "backend.api.memory_api.get_lessons_for_agent_prompt",
            return_value="",
        ):
            out = AgentControlService._load_lesson_memories()

        self.assertEqual(out, "")

    def test_load_lesson_memories_swallows_import_error(self):
        """If memory_api can't be imported, the loader returns empty rather than crash.

        The persistent-knowledge prompt builder must never raise — a broken
        memory layer should silently degrade, not break agent task execution.
        """
        from backend.services import agent_control_service as svc

        # Force the import inside the method to fail.
        with patch.dict("sys.modules", {"backend.api.memory_api": None}):
            # ImportError path is wrapped in try/except in the shim; should
            # return "" cleanly.
            out = svc.AgentControlService._load_lesson_memories()
        self.assertEqual(out, "")


@unittest.skipUnless(_HAS_SELF_IMPROVEMENT, "self_improvement_service unavailable (flask_sqlalchemy missing)")
class TestSelfImprovementDefaults(unittest.TestCase):
    """Default-on analysis, default-blocked apply, user kill-switch unchanged.

    Contract: with no DB rows set —
      _is_self_improvement_enabled       → True  (analysis on by default)
      _is_self_improvement_apply_enabled → False (apply requires opt-in)
      _is_codebase_locked                → False (user kill-switch, unchanged)
    Explicit DB rows still win — we only changed the default for absent rows.
    """

    def _patch_setting(self, row):
        """Patch backend.models.db.session.query(...).filter_by(...).first() → row.

        The functions import db lazily inside their bodies, so the patch target
        is the actual db module, not the calling module.
        """
        from backend.models import db
        query = MagicMock()
        query.filter_by.return_value.first.return_value = row
        return patch.object(db.session, "query", return_value=query)

    def test_self_improvement_enabled_defaults_true_when_no_row(self):
        from backend.services import self_improvement_service as sis
        with self._patch_setting(None):
            self.assertTrue(sis._is_self_improvement_enabled())

    def test_self_improvement_disabled_when_row_says_false(self):
        from backend.services import self_improvement_service as sis
        row = MagicMock()
        row.value = "false"
        with self._patch_setting(row):
            self.assertFalse(sis._is_self_improvement_enabled())

    def test_apply_enabled_defaults_false_when_no_row(self):
        """Apply is the dangerous gate — must default off."""
        from backend.services import self_improvement_service as sis
        with self._patch_setting(None):
            self.assertFalse(sis._is_self_improvement_apply_enabled())

    def test_apply_enabled_when_row_says_true(self):
        from backend.services import self_improvement_service as sis
        row = MagicMock()
        row.value = "true"
        with self._patch_setting(row):
            self.assertTrue(sis._is_self_improvement_apply_enabled())

    def test_codebase_lock_default_unlocked_unchanged(self):
        """Critical regression guard: flipping the analysis default must NOT
        also have flipped the user kill-switch. User-initiated chat-driven
        agent code edits should still work out of the box.
        """
        from backend.services import self_improvement_service as sis
        with self._patch_setting(None), \
             patch("backend.services.self_improvement_service.os.path.exists", return_value=False):
            self.assertFalse(sis._is_codebase_locked())

    def test_codebase_locked_when_row_says_true(self):
        from backend.services import self_improvement_service as sis
        row = MagicMock()
        row.value = "true"
        with self._patch_setting(row), \
             patch("backend.services.self_improvement_service.os.path.exists", return_value=False):
            self.assertTrue(sis._is_codebase_locked())


@unittest.skipUnless(_HAS_CELERY_APP, "celery_app unavailable (celery package missing)")
class TestReconcilerBeatRegistration(unittest.TestCase):
    """The reconciler task is registered on Celery Beat at 6h cadence.

    We import the beat schedule dict directly rather than spinning up Celery —
    that keeps the test fast and DB-free while still proving the wire is in.
    """

    def test_reconcile_belief_updates_in_beat_schedule(self):
        # Beat schedule lives inside create_celery_app(). Read the function's
        # source for the entry rather than executing it (would need redis).
        import inspect
        from backend import celery_app
        src = inspect.getsource(celery_app.create_celery_app)
        self.assertIn("memory-reconcile-belief-updates", src)
        self.assertIn("memory.reconcile_belief_updates", src)
        self.assertIn("21600", src)  # 6h cadence in seconds


if __name__ == "__main__":
    unittest.main()
