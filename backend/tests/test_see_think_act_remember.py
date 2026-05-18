"""
Tests for the see-think-act-remember loop (plans/2026-05-10-see-think-act-remember-loop-design.md).

Covers Phase 2 (FailureReport collation + history formatter) and Phase 3
(observe-only re-grounding + WORLD_OBSERVED injection). Phase 1 is data-file
content and gets a regression check that the priming language is gone.
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


class TestPhase1Priming(unittest.TestCase):
    """Phase 1: verify the priming has actually left the building."""

    def test_self_knowledge_files_no_hardcoded_coords(self):
        from backend.config import GUAARDVARK_ROOT
        for name in ("self_knowledge.md", "self_knowledge_compact.md"):
            path = os.path.join(GUAARDVARK_ROOT, "data", "agent", name)
            with open(path) as f:
                body = f.read()
            self.assertNotIn("x=92", body, f"{name}: pixel coord leaked back in")
            self.assertNotIn("y=103", body, f"{name}: pixel coord leaked back in")
            self.assertNotIn("always visible", body,
                             f"{name}: declarative UI claim ('always visible') leaked back in")

    def test_recipes_have_preconditions_on_desktop_launchers(self):
        from backend.config import GUAARDVARK_ROOT
        path = os.path.join(GUAARDVARK_ROOT, "data", "agent", "recipes.json")
        with open(path) as f:
            recipes = json.load(f)
        # Recipes whose first step is "click the Firefox icon on the desktop"
        # MUST declare a precondition; without it they'll fire even when
        # Firefox is already running and the desktop is covered.
        for name in ("open_firefox", "open_firefox_and_navigate", "open_youtube",
                     "open_reddit", "open_subreddit", "youtube_search"):
            self.assertIn(name, recipes, f"missing recipe: {name}")
            pre = recipes[name].get("preconditions") or []
            self.assertIn(
                "firefox_not_running", pre,
                f"recipe {name} clicks a desktop launcher without a "
                f"firefox_not_running precondition — it'll fire even when "
                f"Firefox covers the desktop column",
            )

    def test_recipes_no_orange_firefox_priming(self):
        """No recipe target_description should hardcode 'orange Firefox button'."""
        from backend.config import GUAARDVARK_ROOT
        path = os.path.join(GUAARDVARK_ROOT, "data", "agent", "recipes.json")
        with open(path) as f:
            body = f.read()
        self.assertNotIn("orange Firefox button", body)
        self.assertNotIn("Shortcuts panel", body)


class TestFailureReport(unittest.TestCase):
    """Phase 2: FailureReport populates from synthetic ActionStep + verification."""

    def setUp(self):
        import backend.services.agent_control_service as acs
        self.svc = acs.AgentControlService()

    def test_failure_report_populates_on_failed_click(self):
        from backend.services.agent_control_service import AgentAction
        action = AgentAction(action_type="click", target_description="primary submit button")
        self.svc._record_failure_report(
            iteration=3,
            action=action,
            result={"success": False, "x": 120, "y": 340},
            pixel_diff=0.0,
            failed=True,
        )
        self.assertEqual(len(self.svc._failure_reports), 1)
        rep = self.svc._failure_reports[0]
        self.assertEqual(rep.iteration, 3)
        self.assertEqual(rep.action_type, "click")
        self.assertEqual(rep.expected_target, "primary submit button")
        self.assertEqual(rep.attempted_at_coords, (120, 340))
        self.assertEqual(rep.screen_delta, 0.0)
        self.assertFalse(rep.dom_match)
        self.assertIn("not on screen", rep.cause_hypothesis)

    def test_failure_report_skipped_on_success(self):
        from backend.services.agent_control_service import AgentAction
        action = AgentAction(action_type="click", target_description="x")
        self.svc._record_failure_report(
            iteration=1, action=action, result={"success": True},
            pixel_diff=0.2, failed=False,
        )
        self.assertEqual(self.svc._failure_reports, [])

    def test_failure_report_capped_at_window(self):
        from backend.services.agent_control_service import AgentAction
        for i in range(10):
            self.svc._record_failure_report(
                iteration=i,
                action=AgentAction(action_type="click", target_description=f"target-{i}"),
                result={},
                pixel_diff=0.0,
                failed=True,
            )
        # Default cap is 5.
        self.assertEqual(len(self.svc._failure_reports), self.svc._failure_reports_cap)
        # Oldest dropped, newest retained.
        self.assertEqual(self.svc._failure_reports[0].iteration, 5)
        self.assertEqual(self.svc._failure_reports[-1].iteration, 9)

    def test_stuck_target_counter_increments(self):
        from backend.services.agent_control_service import AgentAction
        for _ in range(3):
            self.svc._record_failure_report(
                iteration=1,
                action=AgentAction(action_type="click", target_description="red box"),
                result={}, pixel_diff=0.0, failed=True,
            )
        self.assertEqual(self.svc._stuck_target, "red box")
        self.assertEqual(self.svc._stuck_target_count, 3)

    def test_stuck_target_counter_resets_on_change(self):
        from backend.services.agent_control_service import AgentAction
        self.svc._record_failure_report(
            iteration=1,
            action=AgentAction(action_type="click", target_description="red box"),
            result={}, pixel_diff=0.0, failed=True,
        )
        self.svc._record_failure_report(
            iteration=2,
            action=AgentAction(action_type="click", target_description="blue circle"),
            result={}, pixel_diff=0.0, failed=True,
        )
        self.assertEqual(self.svc._stuck_target, "blue circle")
        self.assertEqual(self.svc._stuck_target_count, 1)


class TestFailureHistoryFormatter(unittest.TestCase):
    """Phase 2: the formatter renders a usable prompt block."""

    def setUp(self):
        import backend.services.agent_control_service as acs
        self.svc = acs.AgentControlService()

    def test_empty_history_returns_empty(self):
        # Must not pad the prompt with "no failures" — that trains the
        # model to gloss over the section.
        self.assertEqual(self.svc._format_failure_history(), "")

    def test_history_emits_one_line_per_failure(self):
        from backend.services.agent_control_service import AgentAction
        for label in ("red box", "blue circle"):
            self.svc._record_failure_report(
                iteration=1,
                action=AgentAction(action_type="click", target_description=label),
                result={}, pixel_diff=0.0, failed=True,
            )
        block = self.svc._format_failure_history()
        self.assertIn("red box", block)
        self.assertIn("blue circle", block)
        self.assertIn("Recent failures", block)
        self.assertIn("do NOT retry the same target", block)


class TestObserveOnlyPass(unittest.TestCase):
    """Phase 3: re-grounding vision call returns a parseable WORLD_OBSERVED block."""

    def setUp(self):
        import backend.services.agent_control_service as acs
        self.svc = acs.AgentControlService()

    def test_observe_only_returns_world_observed_block(self):
        from backend.services.agent_control_service import AgentControlService
        # Mock the screen capture + vision analyzer.
        fake_screen = MagicMock()
        with patch.object(
            AgentControlService,
            "_capture_with_retry",
            return_value=(MagicMock(), (0, 0)),
        ), patch("backend.utils.vision_analyzer.VisionAnalyzer") as VA:
            instance = VA.return_value
            instance.analyze.return_value = MagicMock(
                success=True,
                description="Firefox window\nsearch bar\nnew tab button\nbookmarks toolbar",
                error="",
            )
            block = self.svc._observe_only_pass(fake_screen)
        self.assertIn("WORLD_OBSERVED", block)
        self.assertIn("Firefox window", block)
        self.assertIn("search bar", block)
        self.assertIn("trust WORLD_OBSERVED", block)

    def test_observe_only_returns_empty_on_vision_failure(self):
        from backend.services.agent_control_service import AgentControlService
        fake_screen = MagicMock()
        with patch.object(
            AgentControlService,
            "_capture_with_retry",
            return_value=(MagicMock(), (0, 0)),
        ), patch("backend.utils.vision_analyzer.VisionAnalyzer") as VA:
            instance = VA.return_value
            instance.analyze.return_value = MagicMock(
                success=False, description="", error="ollama timeout",
            )
            block = self.svc._observe_only_pass(fake_screen)
        # Best-effort — failure must not block the loop.
        self.assertEqual(block, "")

    def test_observe_only_returns_empty_on_capture_failure(self):
        from backend.services.agent_control_service import AgentControlService
        fake_screen = MagicMock()
        with patch.object(
            AgentControlService,
            "_capture_with_retry",
            side_effect=RuntimeError("display down"),
        ):
            block = self.svc._observe_only_pass(fake_screen)
        self.assertEqual(block, "")


class TestPreconditionsCheck(unittest.TestCase):
    """Phase 1 wiring: preconditions gate recipes from firing."""

    def setUp(self):
        import backend.services.agent_control_service as acs
        self.svc = acs.AgentControlService()

    def test_no_preconditions_passes(self):
        self.assertTrue(self.svc._preconditions_pass({}, MagicMock()))
        self.assertTrue(self.svc._preconditions_pass({"preconditions": []}, MagicMock()))

    def test_firefox_not_running_passes_when_firefox_absent(self):
        with patch.object(self.svc, "_is_firefox_running", return_value=False):
            self.assertTrue(self.svc._preconditions_pass(
                {"preconditions": ["firefox_not_running"]}, MagicMock(),
            ))

    def test_firefox_not_running_fails_when_firefox_present(self):
        with patch.object(self.svc, "_is_firefox_running", return_value=True):
            self.assertFalse(self.svc._preconditions_pass(
                {"preconditions": ["firefox_not_running"]}, MagicMock(),
            ))

    def test_unknown_precondition_does_not_block(self):
        # Typo'd or future-named preconditions must not silently block —
        # warn and proceed so a typo doesn't kill an entire recipe.
        with patch.object(self.svc, "_is_firefox_running", return_value=False):
            self.assertTrue(self.svc._preconditions_pass(
                {"preconditions": ["nonsense_gate"]}, MagicMock(),
            ))


if __name__ == "__main__":
    unittest.main()
