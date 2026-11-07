#!/usr/bin/env python3

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


class TestAgentAction(unittest.TestCase):

    def test_agent_action_click(self):
        from backend.services.agent_control_service import AgentAction
        action = AgentAction(action_type="click", target_cell="D4", target_description="Tweet button")
        self.assertEqual(action.action_type, "click")
        self.assertEqual(action.target_cell, "D4")

    def test_agent_action_type_text(self):
        from backend.services.agent_control_service import AgentAction
        action = AgentAction(action_type="type", text="hello")
        self.assertEqual(action.action_type, "type")
        self.assertEqual(action.text, "hello")


class TestAgentControlConfig(unittest.TestCase):

    def test_default_config(self):
        from backend.services.agent_control_service import AgentControlConfig
        config = AgentControlConfig()
        # Ceilings, not the normal exit: the loop stops on the stall rule.
        self.assertEqual(config.max_iterations, 40)
        self.assertEqual(config.task_timeout_seconds, 480)
        self.assertEqual(config.max_stall_steps, 4)
        self.assertEqual(config.verify_actions, True)
        self.assertEqual(config.grid_cols, 8)
        self.assertEqual(config.grid_rows, 8)
        self.assertEqual(config.vision_model, "gemma4:e4b")
        self.assertEqual(config.max_consecutive_failures, 5)
        self.assertFalse(config.prior_run_note_enabled, "the look-back line is opt-in until measured")


class TestAgentModeState(unittest.TestCase):

    def setUp(self):
        # Reset singleton between tests (follows BrowserAutomationService pattern)
        import backend.services.agent_control_service as acs
        acs._service_instance = None

    def test_initial_state_is_inactive(self):
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        self.assertFalse(service.is_active)

    def test_start_sets_active(self):
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        service._active = True
        self.assertTrue(service.is_active)

    def test_kill_switch(self):
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        service._active = True
        service.kill()
        self.assertFalse(service.is_active)
        self.assertTrue(service._killed)


class TestBuildVisionPrompt(unittest.TestCase):

    def test_builds_scene_analysis_prompt(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        prompt = service._build_vision_prompt("Post hello to Twitter", [])
        self.assertIn("describe the screen", prompt.lower())
        self.assertIn("interactive element", prompt.lower())

    def test_includes_task_context(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        prompt = service._build_vision_prompt("Post hello to Twitter", [])
        self.assertIn("Post hello to Twitter", prompt)


class TestParseDecision(unittest.TestCase):

    def test_parse_click_decision(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        llm_output = '{"action": "click", "target_cell": "D4", "target_description": "Tweet button", "reasoning": "Need to click Tweet"}'
        decision = service._parse_decision(llm_output)
        self.assertEqual(decision.action.action_type, "click")
        self.assertEqual(decision.action.target_cell, "D4")

    def test_parse_type_decision(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        llm_output = '{"action": "type", "text": "Hello world", "reasoning": "Typing message"}'
        decision = service._parse_decision(llm_output)
        self.assertEqual(decision.action.action_type, "type")
        self.assertEqual(decision.action.text, "Hello world")

    def test_parse_done_decision(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        llm_output = '{"action": "done", "reasoning": "Task completed successfully"}'
        decision = service._parse_decision(llm_output)
        self.assertTrue(decision.task_complete)

    def test_parse_done_with_weak_proof_and_prior_verified_step(self):
        """Documents + lightly exercises the grounding + advisory done path (core of the grounded-proof fix).
        has_recent_verified + grounding from ActionStep.result + advisory (non-failed) handling live in
        execute_task done block (see 820-904 area + hoisted detection + proof grounding). The parser
        surfaces success_proof; full advisory contract + "done (advisory...)" emit exercised via e2e mocks
        and manual ChatPage+AgentScreen runs per plan verification section.
        """
        from backend.services.agent_control_service import AgentControlService, ActionStep, AgentAction
        service = AgentControlService()
        # Simulate history after a servo-verified click on the specific target (DPC "verified":True
        # or post_action_effect containing "verified" — exactly what click_target + fast path produce).
        prior_step = ActionStep(
            iteration=0,
            scene_description="youtube search results with thumbnails",
            action=AgentAction(action_type="click", target_description="first video thumbnail"),
            result={"success": True, "verified": True, "post_action_effect": "verified", "verifier": "servo_region_dpc"},
            failed=False,
        )
        service._action_history = [prior_step]
        # Model emits done with weak/empty proof (the case that previously hard-rejected even after goal achieved).
        llm_output = '{"action": "done", "success_proof": "", "reasoning": "I clicked the video and the player is now there"}'
        decision = service._parse_decision(llm_output)
        self.assertTrue(decision.task_complete)
        # In execute_task the has_recent_verified block grounds the proof from the prior target; a
        # grounded proof must then be seen on screen and never takes the advisory path.
        # We assert the objects here; runtime advisory/grounding covered by higher-level tests + manual.

    def test_parse_invalid_json_returns_stuck(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        decision = service._parse_decision("not valid json at all")
        self.assertTrue(decision.stuck)


class TestGetStatus(unittest.TestCase):

    def test_status_returns_dict(self):
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        status = service.get_status()
        self.assertIn("active", status)
        self.assertIn("killed", status)
        self.assertIn("current_task", status)
        self.assertIn("iteration", status)


if __name__ == "__main__":
    unittest.main()


def _click(target, ok=True, action_type="click"):
    from backend.services.agent_control_service import ActionStep, AgentAction
    return ActionStep(action=AgentAction(action_type=action_type, target_description=target), failed=not ok)


def _prompt_svc():
    from backend.services.agent_control_service import AgentControlService
    svc = AgentControlService()
    svc._pending_world_observed = ""
    svc._failure_reports = []
    svc._current_budget = None
    return svc


class TestTaskMemory(unittest.TestCase):
    """The model must see every step of the current task.

    2026-09-23: shown only its last three steps, gemma4:12b clicked A, B, C, D
    perfectly and then cycled A-B-C-D until the timeout, never reaching E,
    because the target that had just scrolled out of the window looked
    pending again. These pin the full list, its counts and the repeat note.
    """

    def setUp(self):
        from backend.services.agent_control_service import AgentControlService
        self.A = AgentControlService

    def test_empty_history_renders_nothing(self):
        self.assertEqual(self.A._history_block([], 15), "")

    def test_every_step_listed_in_order_with_counts(self):
        hist = [_click(f"dot {c}") for c in "ABCDEF"]
        block = self.A._history_block(hist, 15)
        lines = block.splitlines()
        self.assertEqual(lines[0], "Done (steps: 6, click attempts: 6):")
        self.assertEqual(lines[1:], [f"  click: dot {c} [OK]" for c in "ABCDEF"])

    def test_cap_truncates_and_says_so(self):
        hist = [_click(f"dot {i}") for i in range(20)]
        block = self.A._history_block(hist, 15)
        lines = block.splitlines()
        self.assertEqual(lines[0], "Done (steps: 20, click attempts: 20; showing last 15):")
        self.assertEqual(len(lines), 16)
        self.assertEqual(lines[1], "  click: dot 5 [OK]")

    def test_click_attempts_count_only_the_click_family(self):
        from backend.services.agent_control_service import ActionStep, AgentAction
        hist = [
            _click("Firefox icon"),
            ActionStep(action=AgentAction(action_type="scroll", scroll_amount=3), failed=True),
            ActionStep(action=AgentAction(action_type="type", text="hello")),
            _click("Post button", ok=False),
        ]
        block = self.A._history_block(hist, 15)
        self.assertTrue(block.startswith("Done (steps: 4, click attempts: 2):"))

    def test_per_line_format_is_the_golden_fixtures(self):
        from backend.services.agent_control_service import ActionStep, AgentAction
        hist = [_click("Firefox icon"),
                ActionStep(action=AgentAction(action_type="scroll", scroll_amount=3), failed=True)]
        lines = self.A._history_block(hist, 15).splitlines()
        self.assertEqual(lines[1], "  click: Firefox icon [OK]")
        self.assertEqual(lines[2], "  scroll:  [FAIL]")

    def test_repeat_block_is_empty_without_a_repeat(self):
        from backend.services.agent_control_service import ActionStep, AgentAction
        hist = [_click("Firefox icon"),
                ActionStep(action=AgentAction(action_type="scroll", scroll_amount=3), failed=True),
                ActionStep(action=AgentAction(action_type="scroll", scroll_amount=3), failed=True)]
        self.assertEqual(self.A._repeat_block(hist), "")

    def test_repeat_block_names_the_repeated_target_and_count(self):
        hist = [_click("red dot A"), _click("blue dot B"), _click("green dot C"),
                _click("orange dot D"), _click("red dot A")]
        block = self.A._repeat_block(hist)
        self.assertIn('"red dot A" x2', block)
        for other in ("blue dot B", "green dot C", "orange dot D"):
            self.assertNotIn(other, block)

    def test_a_failed_attempt_then_a_hit_is_not_a_repeat(self):
        hist = [_click("red dot A", ok=False), _click("red dot A")]
        self.assertEqual(self.A._repeat_block(hist), "")

    def test_repeat_key_ignores_case_and_whitespace(self):
        hist = [_click("Red dot A"), _click(" red dot a ")]
        self.assertIn('"Red dot A" x2', self.A._repeat_block(hist))

    def test_unified_prompt_at_step_five_shows_all_four_targets(self):
        from unittest.mock import patch
        svc = _prompt_svc()
        hist = [_click(t) for t in ("red dot A", "blue dot B", "green dot C", "orange dot D")]
        with patch.object(self.A, "_get_desktop_state", staticmethod(lambda display=None: "Desktop: fixture")), \
             patch.object(self.A, "_format_dom_grounding_for_prompt", lambda self: ""):
            p = svc._build_unified_prompt("click once in each of the dots", hist)
        for t in ("red dot A", "blue dot B", "green dot C", "orange dot D"):
            self.assertIn(f"  click: {t} [OK]", p)
        self.assertIn("Step 5.", p)
        self.assertIn("Done (steps: 4, click attempts: 4):", p)

    def test_repeat_note_is_suppressed_in_training_mode(self):
        from unittest.mock import patch
        svc = _prompt_svc()
        hist = [_click("colored circle") for _ in range(3)]
        with patch.object(self.A, "_get_desktop_state", staticmethod(lambda display=None: "Desktop: fixture")), \
             patch.object(self.A, "_format_dom_grounding_for_prompt", lambda self: ""):
            training = svc._build_unified_prompt("practice", hist, training_mode=True)
            normal = svc._build_unified_prompt("practice", hist, training_mode=False)
        self.assertNotIn("Already clicked", training)
        self.assertIn('Already clicked [OK] more than once: "colored circle" x3', normal)

    def test_history_cap_is_the_configured_max_iterations(self):
        from unittest.mock import patch
        svc = _prompt_svc()
        svc.config.max_iterations = 4
        hist = [_click(f"dot {i}") for i in range(6)]
        with patch.object(self.A, "_get_desktop_state", staticmethod(lambda display=None: "Desktop: fixture")), \
             patch.object(self.A, "_format_dom_grounding_for_prompt", lambda self: ""):
            p = svc._build_unified_prompt("t", hist)
        self.assertIn("showing last 4", p)


def _stepped(target, ok=True, action_type="click", verified=False, effect=None):
    from backend.services.agent_control_service import ActionStep, AgentAction
    result = {"success": ok, "verified": verified}
    if effect:
        result["post_action_effect"] = effect
    return ActionStep(action=AgentAction(action_type=action_type, target_description=target),
                      result=result, failed=not ok)


class TestStallRule(unittest.TestCase):
    """The loop stops when progress stops, not at a fixed count.

    Progress: a verified screen change, or a click on a target not yet clicked
    [OK] this task. Failed steps have their own guard and do not count.
    """

    def setUp(self):
        from backend.services.agent_control_service import AgentControlService
        self.A = AgentControlService
        self.svc = AgentControlService()

    def _run(self, steps, training_mode=False):
        """Feed steps through the loop's own counter; return the 1-based step
        at which it says stop, or None."""
        self.svc._action_history = []
        self.svc._stall_steps = 0
        for i, st in enumerate(steps, 1):
            self.svc._action_history.append(st)
            if self.svc._note_progress(st, training_mode=training_mode):
                return i
        return None

    def test_progress_truth_table(self):
        A = _stepped("red dot A")
        self.assertTrue(self.A._step_progress(A, []))
        self.assertFalse(self.A._step_progress(_stepped("red dot A"), [A]), "repeat, no change")
        self.assertTrue(self.A._step_progress(_stepped("red dot A", verified=True), [A]), "repeat with a change")
        self.assertTrue(self.A._step_progress(_stepped("red dot A", effect="verified"), [A]))
        self.assertFalse(self.A._step_progress(_stepped("red dot A", ok=False), []), "failed never counts")
        self.assertTrue(self.A._step_progress(_stepped("", action_type="type", verified=True), []))
        self.assertFalse(self.A._step_progress(_stepped("", action_type="type"), []))
        self.assertFalse(self.A._step_progress(_stepped("", action_type="wait"), []))
        self.assertTrue(self.A._step_progress(_stepped("Red Dot A "), [_stepped("red dot a", ok=False)]),
                        "a target only ever missed is still new")

    def test_replays_the_2026_09_23_cycle_and_stops_at_step_nine(self):
        # run 242cd07c: A B C D E, then A B C D E, A, B — all hits, none changed the screen.
        steps = [_stepped(t) for t in ("A", "B", "C", "D", "E", "A", "B", "C", "D", "E", "A", "B")]
        self.assertEqual(self._run(steps), 9)
        self.assertEqual(self.svc._stall_steps, 4)

    def test_five_new_targets_never_stall(self):
        self.assertIsNone(self._run([_stepped(t) for t in "ABCDE"]))
        self.assertEqual(self.svc._stall_steps, 0)

    def test_a_verified_change_resets_the_counter(self):
        steps = [_stepped("A"), _stepped("A"), _stepped("A"), _stepped("A", verified=True),
                 _stepped("A"), _stepped("A"), _stepped("A")]
        self.assertIsNone(self._run(steps))
        self.assertEqual(self.svc._stall_steps, 3)

    def test_failed_steps_do_not_count_toward_a_stall(self):
        steps = [_stepped("A")] + [_stepped("B", ok=False)] * 6
        self.assertIsNone(self._run(steps))

    def test_training_mode_never_stalls(self):
        self.assertIsNone(self._run([_stepped("colored circle")] * 20, training_mode=True))

    def test_the_threshold_is_the_configured_one(self):
        self.svc.config.max_stall_steps = 2
        self.assertEqual(self._run([_stepped("A"), _stepped("A"), _stepped("A")]), 3)


class TestRepetitionVerdict(unittest.TestCase):
    """Three identical steps end the task; it is a success only when none
    failed and at least one changed the screen."""

    def _verdict(self, steps):
        from backend.services.agent_control_service import AgentControlService
        return AgentControlService._repetition_verdict(steps)

    def test_three_clicks_that_changed_nothing_are_no_progress(self):
        steps = [_stepped("Zebra", effect="no_visible_change") for _ in range(3)]
        self.assertEqual(self._verdict(steps), (False, "loop_detected_no_progress"))
        steps = [_stepped("Zebra", effect="not_observed") for _ in range(3)]
        self.assertEqual(self._verdict(steps), (False, "loop_detected_no_progress"))

    def test_three_verified_clicks_keep_completed_with_repetition(self):
        steps = [_stepped("Like", verified=True, effect="verified") for _ in range(3)]
        self.assertEqual(self._verdict(steps), (True, "completed_with_repetition"))

    def test_one_change_among_three_is_enough(self):
        steps = [_stepped("Next", effect="no_visible_change"), _stepped("Next", effect="no_visible_change"),
                 _stepped("Next", verified=True, effect="verified")]
        self.assertEqual(self._verdict(steps), (True, "completed_with_repetition"))

    def test_a_failed_step_is_no_progress(self):
        steps = [_stepped("Like", verified=True, effect="verified"), _stepped("Like", ok=False),
                 _stepped("Like", verified=True, effect="verified")]
        self.assertEqual(self._verdict(steps), (False, "loop_detected_no_progress"))


class TestHonestActions(unittest.TestCase):
    """What the loop sends and what it tells the model, from the 2026-10-02
    YouTube comment run (episode 5e877afc): scrolls with no amount sent no
    wheel clicks, text typed with nothing focused hit YouTube's single-key
    shortcuts, and a target the eye never found was retried 16 times."""

    def setUp(self):
        from backend.services.agent_control_service import AgentControlService
        self.svc = AgentControlService()
        self.svc._action_history = []

    def _add(self, kind, target="", coords=None, failed=False, result=None, text="", keys=None):
        from backend.services.agent_control_service import ActionStep, AgentAction
        a = AgentAction(action_type=kind, target_description=target, text=text, keys=keys or [])
        a.coordinates = coords
        st = ActionStep(action=a, result=result if result is not None else {"success": not failed},
                        failed=failed)
        self.svc._action_history.append(st)
        return st

    def _refusal(self, kind, **kw):
        from backend.services.agent_control_service import AgentAction
        return self.svc._refusal_for(AgentAction(action_type=kind, **kw))

    def test_scroll_without_an_amount_scrolls_down(self):
        parse = self.svc._parse_decision
        self.assertEqual(parse('{"action": "scroll"}').action.scroll_amount, -5)
        self.assertEqual(parse('{"action": "scroll", "scroll_amount": 0}').action.scroll_amount, -5)
        self.assertEqual(parse('{"action": "scroll", "scroll_amount": 3}').action.scroll_amount, 3)
        self.assertEqual(parse('{"action": "scroll", "scroll_amount": 4, "direction": "down"}').action.scroll_amount, -4)
        self.assertEqual(parse('{"action": "scroll", "direction": "up"}').action.scroll_amount, 5)
        self.assertEqual(parse('{"action": "scroll", "scroll_amount": -99}').action.scroll_amount, -15)

    def test_wait_keeps_its_seconds_in_scroll_amount(self):
        self.assertEqual(self.svc._parse_decision('{"action": "wait"}').action.scroll_amount, 0)

    def test_type_with_no_field_clicked_is_not_sent(self):
        self._add("click", "first video thumbnail", coords=(321, 322),
                  result={"success": True, "click_issued": True, "verified": True})
        self._add("scroll", result={"success": True, "verified": True})
        self.assertIn("no text field has been clicked", self._refusal("type", text="Check out"))

    def test_type_after_clicking_something_that_is_not_a_field_is_not_sent(self):
        self._add("click", "first video thumbnail", coords=(321, 322),
                  result={"success": True, "click_issued": True})
        self.assertIn("not a text field", self._refusal("type", text="Check out"))

    def test_type_after_clicking_a_field_is_sent_and_remembers_where(self):
        self._add("click", "Add a comment box", coords=(400, 700),
                  result={"success": True, "click_issued": True})
        self.assertEqual(self._refusal("type", text="hello"), "")
        self.assertEqual(self.svc._field_point, (400, 700))

    def test_type_after_a_focus_hotkey_is_sent(self):
        self._add("hotkey", keys=["ctrl", "l"])
        self.assertEqual(self._refusal("type", text="example.com"), "")

    def test_enter_after_a_failed_type_is_not_sent(self):
        self._add("type", text="hello", failed=True,
                  result={"success": False, "reason": "typed_text_not_in_field"})
        self.assertIn("Enter would act on the page", self._refusal("hotkey", keys=["Return"]))

    def test_a_hotkey_with_no_keys_is_not_sent(self):
        self.assertEqual(self._refusal("hotkey", keys=[]), "the hotkey had no keys")

    def test_a_target_not_found_three_times_is_held_back_and_six_stops(self):
        nf = {"success": False, "target_found": False, "click_issued": False,
              "reason": "target_not_visible"}
        for _ in range(2):
            self._add("click", "comment input field", failed=True, result=dict(nf))
            self.svc._note_not_found("comment input field")
        self.assertEqual(self.svc._banned_targets, {})
        st = self._add("click", "comment input field", failed=True, result=dict(nf))
        self.svc._note_not_found("comment input field")
        self.assertEqual(self.svc._step_status(st), "NOT ON SCREEN")
        self.assertIn("comment input field", self.svc._banned_targets)
        self.assertIn("was not on screen the last 3 times",
                      self._refusal("click", target_description="The comment input field"))
        self.assertEqual(self._refusal("click", target_description="Add a comment box"), "")
        self.assertIn('NOT ON SCREEN: "comment input field" (3 looks)', self.svc._not_found_block())
        self.assertIsNone(self.svc._target_given_up())
        for _ in range(3):
            self.svc._note_not_found("comment input field")
        self.assertEqual(self.svc._target_given_up(), ("comment input field", 6))
        self.assertEqual(self.svc._stop_rule_from_reason("target_not_found: 'x' was not on screen"),
                         "not_found")

    def test_a_cooldown_blocks_the_failed_target_not_every_click(self):
        from backend.services.agent_control_service import AgentAction
        post = AgentAction(action_type="click", target_description="Post button")
        self.svc._record_strategy_outcome(post, True)
        self.svc._record_strategy_outcome(post, True)
        self.assertIn("failed twice", self._refusal("click", target_description="Post button"))
        self.assertEqual(self._refusal("click", target_description="Cancel button"), "")

    def test_refused_and_not_found_steps_say_so_to_the_model(self):
        st = self._add("type", text="hi", failed=True,
                       result={"success": False, "refused": True, "refusal": "no text field has been clicked"})
        self.assertEqual(self.svc._step_status(st), "NOT SENT")
        signal = self.svc._semantic_progress_signal(st.action, st.result, failed=True, pixel_diff=None)
        self.assertEqual(signal.label, "action_refused")
        nf = self._add("click", "comment input field", failed=True,
                       result={"success": False, "target_found": False, "reason": "target_not_visible"})
        signal = self.svc._semantic_progress_signal(nf.action, nf.result, failed=True, pixel_diff=None)
        self.assertIn("is not on screen", signal.evidence)

    def test_pivot_block_offers_no_named_click_targets(self):
        for _ in range(2):
            self._add("click", "comment input field", failed=True)
        block = self.svc._pivot_block(self.svc._action_history)
        self.assertIn("STOP.", block)
        self.assertNotIn('"target_description"', block)

    def test_pivot_block_does_not_offer_the_key_that_just_failed(self):
        for _ in range(2):
            self._add("hotkey", keys=["Page_Down"], failed=True)
        block = self.svc._pivot_block(self.svc._action_history)
        self.assertNotIn('"Page_Down"', block)
        self.assertIn('"End"', block)


class TestScreenChange(unittest.TestCase):
    """The before/after check that decides whether a type, scroll or hotkey
    did anything, with what moves by itself (a playing video) left out."""

    def setUp(self):
        from PIL import Image, ImageDraw
        from backend.services.agent_control_service import AgentControlService
        self.A = AgentControlService
        self.Image, self.Draw = Image, ImageDraw
        self.page = Image.new("RGB", (400, 400), "white")
        d = ImageDraw.Draw(self.page)
        for y in range(200, 400, 20):
            d.text((10, y), "some page text on a line", fill="black")

    def _video(self, seed):
        # Grey levels far enough apart (80) to clear the 24-level change threshold.
        out = self.page.copy()
        grey = (40, 120, 200)[(seed - 1) % 3]
        self.Draw.Draw(out).rectangle([0, 0, 399, 150], fill=(grey, grey, grey))
        return out

    def test_a_playing_video_is_not_a_change(self):
        change = self.A._screen_change(self._video(1), self._video(2), self._video(3))
        self.assertEqual(change["changed_blocks"], 0)
        self.assertGreater(change["live_blocks"], 0)

    def test_a_scroll_that_moved_the_page_is_a_change(self):
        moved = self.Image.new("RGB", (400, 400), "white")
        moved.paste(self.page.crop((0, 100, 400, 400)), (0, 0))
        change = self.A._screen_change(self.page, self.page, moved)
        self.assertGreaterEqual(change["share"], self.A._SCROLL_MIN_SHARE)

    def test_typed_text_is_counted_near_the_field(self):
        typed = self.page.copy()
        self.Draw.Draw(typed).text((20, 170), "hello there", fill="black")
        change = self.A._screen_change(self.page, self.page, typed, near=(60, 175))
        self.assertGreaterEqual(change["near_blocks"], self.A._CHANGE_MIN_BLOCKS)
        far = self.A._screen_change(self.page, self.page, typed, near=(60, 2000))
        self.assertEqual(far["near_blocks"], 0)

    def test_a_scrollbar_flash_at_the_bottom_of_a_page_is_not_a_scroll(self):
        flashed = self.page.copy()
        self.Draw.Draw(flashed).rectangle([392, 0, 399, 399], fill="gray")
        change = self.A._screen_change(self.page, self.page, flashed)
        self.assertLess(change["width_share"], self.A._SCROLL_MIN_WIDTH)

    def test_scroll_blocks_are_kept_apart_by_direction(self):
        from backend.services.agent_control_service import AgentAction
        svc = self.A()
        down = AgentAction(action_type="scroll", scroll_amount=-5)
        svc._record_strategy_outcome(down, True)
        svc._record_strategy_outcome(down, True)
        self.assertIn("failed twice", svc._refusal_for(down))
        self.assertEqual(svc._refusal_for(AgentAction(action_type="scroll", scroll_amount=5)), "")

    def test_frames_of_different_sizes_are_not_compared(self):
        self.assertIsNone(self.A._screen_change(self.page, self.page, self.Image.new("RGB", (10, 10))))


class TestNotesWhileWorking(unittest.TestCase):
    """Notes the user sends while a screen task runs: read at the next step,
    kept in the prompt, and never a new task that kills the running one."""

    def setUp(self):
        from backend.services.agent_control_service import AgentControlService
        self.svc = AgentControlService()

    def _running(self, session_id="s1"):
        self.svc._active = True
        self.svc._killed = False
        self.svc._notes_open = True
        self.svc._task_session_id = session_id
        self.svc._current_iteration = 2

    def test_no_task_running_means_not_queued(self):
        self.assertEqual(self.svc.add_steer_note("go left", "s1"),
                         {"queued": False, "reason": "no_active_task"})

    def test_a_note_is_queued_read_once_and_kept_in_the_prompt(self):
        self._running()
        out = self.svc.add_steer_note("  say guaardvark.com instead ", "s1")
        self.assertTrue(out["queued"])
        self.assertEqual(self.svc._steer_block(), "", "not in the prompt before the loop reads it")
        new = self.svc._take_new_notes(4)
        self.assertEqual([n["text"] for n in new], ["say guaardvark.com instead"])
        self.assertEqual(self.svc._take_new_notes(5), [], "read once")
        block = self.svc._steer_block()
        self.assertIn('NEW (read at step 4) "say guaardvark.com instead"', block)
        self.svc.add_steer_note("and keep it short", "s1")
        self.svc._take_new_notes(6)
        block = self.svc._steer_block()
        self.assertIn('  - (read at step 4) "say guaardvark.com instead"', block)
        self.assertIn('NEW (read at step 6) "and keep it short"', block)

    def test_a_note_from_another_chat_is_refused(self):
        self._running("s1")
        self.assertEqual(self.svc.add_steer_note("hi", "s2")["reason"], "other_session")

    def test_stop_ends_the_task_and_other_words_do_not(self):
        self._running()
        self.assertTrue(self.svc.add_steer_note("Stop!", "s1")["stopping"])
        self.assertTrue(self.svc._killed)
        self._running()
        out = self.svc.add_steer_note("stop clicking the logo and scroll down", "s1")
        self.assertNotIn("stopping", out)
        self.assertFalse(self.svc._killed)

    def test_notes_close_when_the_task_finishes(self):
        self._running()
        self.svc.add_steer_note("one more thing", "s1")
        self.svc._notes_open = False
        self.assertEqual(self.svc.add_steer_note("too late", "s1")["reason"], "no_active_task")
        self.assertEqual(self.svc._late_notes(), ["one more thing"])

    def test_notes_are_bounded(self):
        self._running()
        for i in range(self.svc._MAX_NOTES):
            self.assertTrue(self.svc.add_steer_note(f"note {i}", "s1")["queued"])
        self.assertEqual(self.svc.add_steer_note("one too many", "s1")["reason"], "too_many_notes")

    def test_unified_prompt_carries_the_notes(self):
        from unittest.mock import patch
        from backend.services.agent_control_service import AgentControlService as A
        self._running()
        self.svc._pending_world_observed = ""
        self.svc._failure_reports = []
        self.svc._current_budget = None
        self.svc.add_steer_note("the comment box is further down", "s1")
        self.svc._take_new_notes(3)
        with patch.object(A, "_get_desktop_state", staticmethod(lambda display=None: "Desktop: fixture")), \
             patch.object(A, "_format_dom_grounding_for_prompt", lambda self: ""):
            p = self.svc._build_unified_prompt("post a comment", [])
        self.assertIn("NOTES FROM THE USER", p)
        self.assertLess(p.index("NOTES FROM THE USER"), p.index("Task: post a comment"))


class TestPointActions(unittest.TestCase):
    """click_at and draw: exact screen points from the model, for tasks that
    give coordinates and for drawing, where the eye has nothing to find."""

    def setUp(self):
        from backend.services.agent_control_service import AgentControlService
        self.A = AgentControlService
        self.svc = AgentControlService()

    def test_click_at_and_draw_parse(self):
        a = self.svc._parse_decision('{"action": "click_at", "x": 420, "y": 320}').action
        self.assertEqual((a.action_type, a.coordinates), ("click_at", (420, 320)))
        a = self.svc._parse_decision('{"action": "click_at", "point": [10.6, 20.2]}').action
        self.assertEqual(a.coordinates, (11, 20))
        a = self.svc._parse_decision(
            '{"action": "draw", "points": [[400, 500], {"x": 450, "y": 530}, "bad", [1]]}').action
        self.assertEqual(a.points, [(400, 500), (450, 530)])

    def test_drag_reads_its_destination(self):
        a = self.svc._parse_decision('{"action": "drag", "target_description": "a", "drag_to": "b"}').action
        self.assertEqual(a.drag_to_description, "b")

    def test_dots_at_different_places_are_not_a_repeat(self):
        from backend.services.agent_control_service import ActionStep, AgentAction
        h = [ActionStep(action=AgentAction(action_type="click_at", coordinates=(420, 320))),
             ActionStep(action=AgentAction(action_type="click_at", coordinates=(640, 320)))]
        self.assertEqual(self.A._pivot_block(h), "")
        same = [ActionStep(action=AgentAction(action_type="click_at", coordinates=(420, 320))) for _ in range(2)]
        self.assertIn("STOP.", self.A._pivot_block(same))

    def test_history_says_where_points_went(self):
        from backend.services.agent_control_service import ActionStep, AgentAction
        h = [ActionStep(action=AgentAction(action_type="click_at", coordinates=(420, 320))),
             ActionStep(action=AgentAction(action_type="draw", points=[(400, 500), (450, 530), (500, 500)]))]
        lines = self.A._history_block(h, 40).splitlines()
        self.assertEqual(lines[0], "Done (steps: 2, click attempts: 1):")
        self.assertEqual(lines[1], "  click_at: at (420, 320) [OK]")
        self.assertEqual(lines[2], "  draw: 3 points from (400, 500) to (500, 500) [OK]")

    def test_a_field_clicked_by_point_can_be_typed_into(self):
        from backend.services.agent_control_service import ActionStep, AgentAction
        self.svc._action_history = [ActionStep(action=AgentAction(action_type="click_at", coordinates=(300, 700)),
                                               result={"success": True})]
        self.assertEqual(self.svc._refusal_for(AgentAction(action_type="type", text="hi")), "")
        self.assertEqual(self.svc._field_point, (300, 700))

    def test_a_verified_stroke_counts_as_a_real_change_for_done(self):
        from backend.services.agent_control_service import ActionStep, AgentAction
        drawn = ActionStep(action=AgentAction(action_type="draw", points=[(1, 1), (9, 9)]),
                           result={"success": True, "verified": True})
        unchanged = ActionStep(action=AgentAction(action_type="click_at", coordinates=(5, 5)),
                               result={"success": True, "verified": False})
        self.assertTrue(self.A._task_has_verified_click([drawn]))
        self.assertFalse(self.A._task_has_verified_click([unchanged]))

    def test_point_actions_are_offered_only_to_coordinate_or_drawing_tasks(self):
        from backend.services.agent_control_service import AgentAction
        wanted = self.A._points_wanted
        self.assertTrue(wanted("one eye on the upper-left (around x=420, y=320)"))
        self.assertTrue(wanted("click at (420, 320)"))
        self.assertTrue(wanted("Draw a smiley face on this blank canvas"))
        self.assertFalse(wanted("Please click once in each of the dots. You have 5 click attempts total."))
        self.assertFalse(wanted("open the second drawer"))
        self.svc._points_on = False
        self.assertIn("use click with a target_description",
                      self.svc._refusal_for(AgentAction(action_type="click_at", coordinates=(1, 2))))
        self.assertNotIn("click_at", self.svc._schema_full())
        self.svc._points_on = True
        self.assertEqual(self.svc._refusal_for(AgentAction(action_type="click_at", coordinates=(1, 2))), "")
        self.assertIn("click|click_at|draw|", self.svc._schema_full())

    def test_the_rule_names_the_screen_size(self):
        self.svc._screen_size = (1000, 1000)
        self.assertIn("the screen is 1000x1000", self.svc._point_rule())


class TestLatestClickVerified(unittest.TestCase):
    """The advisory "done" reads only the latest click since the last
    navigate; an earlier verified click does not vouch for it."""

    def _has(self, history):
        from backend.services.agent_control_service import AgentControlService
        return AgentControlService._task_has_verified_click(history)

    def test_verified_composer_then_unverified_post_is_not_verified(self):
        history = [_stepped("composer", verified=True, effect="verified"),
                   _stepped("Post button", effect="no_visible_change")]
        self.assertFalse(self._has(history))

    def test_verified_post_last_is_verified(self):
        history = [_stepped("composer", verified=True, effect="verified"),
                   _stepped("Post button", verified=True, effect="verified")]
        self.assertTrue(self._has(history))

    def test_a_failed_last_click_is_not_verified(self):
        history = [_stepped("composer", verified=True, effect="verified"),
                   _stepped("Post button", ok=False)]
        self.assertFalse(self._has(history))

    def test_steps_that_are_not_clicks_do_not_hide_the_last_click(self):
        history = [_stepped("Post button", verified=True, effect="verified"),
                   _stepped("", action_type="hotkey"), _stepped("", action_type="wait")]
        self.assertTrue(self._has(history))

    def test_a_navigate_after_the_click_clears_it(self):
        history = [_stepped("Post button", verified=True, effect="verified"),
                   _stepped("", action_type="navigate")]
        self.assertFalse(self._has(history))


class TestLatestClickKind(unittest.TestCase):
    """Which kind of click the advisory "done" would lean on: a drawn mark
    stands on its own, a plain click gets a second look."""

    def _kind(self, history):
        from backend.services.agent_control_service import AgentControlService
        return AgentControlService._latest_click_kind(history)

    def test_plain_click_after_a_draw_is_a_click(self):
        history = [_stepped("smiley", action_type="draw", verified=True),
                   _stepped("Documents folder icon", verified=True, effect="verified")]
        self.assertEqual(self._kind(history), "click")

    def test_draw_last_is_a_draw(self):
        history = [_stepped("Documents folder icon", verified=True),
                   _stepped("smiley", action_type="draw", verified=True), _stepped("", action_type="wait")]
        self.assertEqual(self._kind(history), "draw")

    def test_navigate_clears_it(self):
        history = [_stepped("Post button", verified=True), _stepped("", action_type="navigate")]
        self.assertIsNone(self._kind(history))


class TestSecondLookDone(unittest.TestCase):
    """The second look answers whether the screen shows the task finished;
    anything but a plain yes, including a failed read, is "not finished"."""

    def _look(self, description, success=True, error=None):
        from backend.services.agent_control_service import AgentControlService
        svc = AgentControlService.__new__(AgentControlService)
        screen = MagicMock()
        screen.capture.return_value = ("img", None)
        result = MagicMock(success=success, description=description, error=error)
        with patch("backend.utils.vision_analyzer.VisionAnalyzer") as va:
            va.return_value.analyze.return_value = result
            va.return_value.default_model = "eye"
            out = svc._second_look_done("Open the Documents folder on the desktop.", screen)
            prompt = va.return_value.analyze.call_args.kwargs["prompt"]
        return out, prompt

    def test_yes_is_finished_and_the_prompt_names_the_task(self):
        out, prompt = self._look("Yes")
        self.assertTrue(out["success"])
        self.assertIn("Open the Documents folder on the desktop.", prompt)

    def test_no_is_not_finished(self):
        out, _ = self._look("no, the Downloads folder is open")
        self.assertFalse(out["success"])

    def test_a_failed_read_is_not_finished(self):
        out, _ = self._look("", success=False, error="model unreachable")
        self.assertFalse(out["success"])


class TestFailedToolsSayWhy(unittest.TestCase):
    """A failed tool's own explanation reaches the model; a block says how
    long it lasts."""

    def test_output_stands_in_for_a_missing_error(self):
        from backend.services.agent_tools import ToolResult
        from backend.utils.agent_output_parser import format_tool_result_for_llm
        r = ToolResult(success=False, output="Task failed after 17 steps (491.3s): timeout")
        xml = format_tool_result_for_llm("agent_task_execute", r, format="xml")
        self.assertIn("Error: Task failed after 17 steps (491.3s): timeout", xml)
        self.assertNotIn("Error: None", xml)

    def test_error_and_output_both_reach_the_model(self):
        import json
        from backend.services.agent_tools import ToolResult
        from backend.utils.agent_output_parser import format_tool_result_for_llm
        r = ToolResult(success=False, error="Task failed: timeout", output="Last steps: click at (1, 2)")
        obs = json.loads(format_tool_result_for_llm("agent_task_execute", r, format="json"))
        self.assertEqual(obs["error"], "Task failed: timeout")
        self.assertEqual(obs["output"], "Last steps: click at (1, 2)")

    def test_block_message_names_its_scope(self):
        from backend.services.tool_execution_guard import ToolExecutionGuard
        g = ToolExecutionGuard(max_failures_per_tool=2, scope="for the rest of this reply")
        for _ in range(2):
            g.record_result("agent_task_execute", {"task": "x"}, False, "timeout", 1)
        allowed, why = g.check_call("agent_task_execute", {"task": "y"})
        self.assertFalse(allowed)
        self.assertIn("is disabled for the rest of this reply", why)


class TestDrawingReachesTheScreen(unittest.TestCase):
    """"Draw ..." in agent mode is a screen task, not an image request; the
    names models invent for the point actions still reach them."""

    def test_agent_mode_draw_goes_to_the_screen(self):
        from backend.services.agent_brain import is_pure_image_request
        msg = "Draw a smiley face on this blank canvas: two eyes and a smiling mouth."
        self.assertFalse(is_pure_image_request(msg, {"agent_mode": True}))
        self.assertFalse(is_pure_image_request(msg, {}), "a canvas is the screen")
        self.assertTrue(is_pure_image_request("draw a cat in a hat", {}))
        self.assertFalse(is_pure_image_request("open the second drawer of the cabinet", {}))
        self.assertTrue(is_pure_image_request("make an image of a lighthouse", {"agent_screen_active": True}))

    def test_invented_action_names_are_mapped(self):
        from backend.services.agent_control_service import AgentControlService
        svc = AgentControlService()
        a = svc._parse_decision('{"action": "draw_stroke", "points": [[1, 2], [3, 4]]}').action
        self.assertEqual((a.action_type, a.points), ("draw", [(1, 2), (3, 4)]))
        a = svc._parse_decision('{"action": "click_point", "x": 5, "y": 6}').action
        self.assertEqual((a.action_type, a.coordinates), ("click_at", (5, 6)))


class TestLessonsAreAboutTheTask(unittest.TestCase):
    """Only lessons about what the task aimed at are written. Replays the
    2026-10-02 YouTube comment run: its targets and the 7 lessons it saved."""

    def setUp(self):
        from flask import Flask
        from backend.services.agent_control_service import (
            AgentControlService, ActionStep, AgentAction, Expectation)
        self.svc = AgentControlService()
        self.svc._action_history = [
            ActionStep(action=AgentAction(action_type="navigate", url="https://youtube.com")),
            ActionStep(action=AgentAction(action_type="click", target_description="first video thumbnail")),
            ActionStep(action=AgentAction(action_type="type", text="Check out the Guaardvark project")),
            ActionStep(action=AgentAction(action_type="click", target_description="comment input field"),
                       failed=True),
        ]
        saved = ["comment input field", "thumbs up icon left of dislike", "Reply button under comment",
                 "empty Add a comment input", "first video result", "desktop", "Firefox icon"]
        self.svc._expectation_log = [
            Expectation(element=e, expected_visible=True, observed_visible=False, source="self_knowledge",
                        source_line=None, confidence=0.5)
            for e in saved
        ]
        self.app = Flask(__name__)

    def test_only_task_targets_are_written(self):
        from unittest.mock import patch
        written = []
        with self.app.app_context(), \
             patch("backend.api.memory_api.add_memory",
                   side_effect=lambda **kw: written.append(kw["metadata"]["element"]) or object()):
            n = self.svc._write_session_lessons()
        self.assertEqual(n, 2)
        self.assertEqual(written, ["comment input field", "empty Add a comment input"])

    def test_no_targets_means_no_lessons(self):
        from unittest.mock import patch
        self.svc._action_history = []
        with self.app.app_context(), patch("backend.api.memory_api.add_memory") as add:
            self.assertEqual(self.svc._write_session_lessons(), 0)
        add.assert_not_called()


class TestSeenMeansListed(unittest.TestCase):
    """A target counts as seen only when a listed element names it, by whole
    word; the re-grounding block's own prose does not count."""

    def test_the_instruction_prose_does_not_make_a_missing_target_seen(self):
        from backend.services.agent_control_service import AgentControlService
        svc = AgentControlService()
        svc._stuck_target, svc._stuck_target_count = "red Subscribe button under video", 2
        observed = (
            "WORLD_OBSERVED (fresh capture, no task bias, no priming):\n"
            "- WatchTube logo\n- Search icon\n- Back button\n"
            "When WORLD_OBSERVED contradicts what you remembered or expected, trust WORLD_OBSERVED."
        )
        svc._record_expectation_contradictions([], observed)
        self.assertEqual([e.element for e in svc._expectation_log], ["red Subscribe button under video"])

    def test_a_listed_element_is_seen(self):
        from backend.services.agent_control_service import AgentControlService
        svc = AgentControlService()
        svc._stuck_target, svc._stuck_target_count = "Subscribe button", 2
        svc._record_expectation_contradictions([], "WORLD_OBSERVED:\n- Red Subscribe buttons row\n")
        self.assertEqual(svc._expectation_log, [])


class TestCheckEarlyDone(unittest.TestCase):
    """A screen task ends early only when the window list shows its goal is
    met; a desktop that could not be read never ends one."""

    FIREFOX = "Desktop state — currently open:\n  - Google — Mozilla Firefox (1280x720 at (0,0))"
    CHROMIUM = "Desktop state — currently open:\n  - New Tab - Chromium (1280x720 at (0,0))"
    EMPTY = "Desktop state: No application windows open. Desktop is empty."
    UNKNOWN = "Desktop state: unknown (query failed)"

    def _check(self, task, desktop):
        from backend.services.agent_control_service import AgentControlService as A
        with patch.object(A, "_get_desktop_state", staticmethod(lambda display=None: desktop)):
            return A._check_early_done(task)

    def test_close_firefox_not_done_while_open(self):
        self.assertEqual(self._check("close firefox", self.FIREFOX), "")

    def test_close_firefox_done_when_gone(self):
        self.assertEqual(self._check("close firefox", self.CHROMIUM), "firefox no longer visible")

    def test_close_browser_not_done_while_chromium_open(self):
        self.assertEqual(self._check("close the browser", self.CHROMIUM), "")

    def test_close_chrome_not_done_while_chromium_open(self):
        self.assertEqual(self._check("close chrome", self.CHROMIUM), "")

    def test_stop_is_not_close_verb(self):
        self.assertEqual(self._check("find the bus stop on the map", self.EMPTY), "")
        self.assertEqual(self._check("stop the video", self.EMPTY), "")

    def test_unknown_state_never_done(self):
        for task in ("close firefox", "close all windows", "open firefox"):
            self.assertEqual(self._check(task, self.UNKNOWN), "", task)

    def test_close_all_empty_desktop_done(self):
        self.assertEqual(self._check("close all windows", self.EMPTY), "no windows open — target closed")

    def test_open_firefox_done_when_visible(self):
        self.assertEqual(self._check("open firefox", self.FIREFOX), "firefox is now open")

    def test_bare_task_with_please_and_punctuation_still_checked(self):
        self.assertEqual(self._check("Please open Firefox.", self.FIREFOX), "firefox is now open")
        self.assertEqual(self._check("close firefox!", self.CHROMIUM), "firefox no longer visible")

    def test_a_task_with_a_second_clause_never_ends_early(self):
        for task in ("Open Firefox and go to reddit.com",
                     "open the settings in Firefox",
                     "Quit Chrome and open Firefox"):
            self.assertEqual(self._check(task, self.FIREFOX), "", task)

    def test_unreachable_display_reads_as_unknown(self):
        from backend.services.agent_control_service import AgentControlService as A
        failed = MagicMock(returncode=1, stdout="", stderr="Error: Can't open display: (null)")
        with patch("subprocess.run", return_value=failed) as run:
            state = A._get_desktop_state(display=":99")
        self.assertTrue(state.startswith("Desktop state: unknown"), state)
        self.assertEqual(run.call_count, 1)


class TestAssessObstacles(unittest.TestCase):
    """ASSESS leaves a page that only mentions cookies alone, and hands an
    obstacle it has already tried twice this task to THINK."""

    CONSENT = "A cookie consent banner covers the bottom of the page with Accept and Reject buttons."

    def setUp(self):
        from backend.services.agent_control_service import AgentControlService
        self.svc = AgentControlService()
        self.screen = MagicMock()
        sleep = patch("time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def _assess(self, scene, iteration=0):
        return self.svc._assess_obstacles(scene, MagicMock(), self.screen, iteration)

    def test_a_page_about_cookies_is_clear(self):
        self.assertEqual(self._assess("a recipe page about chocolate chip cookies"), "clear")
        self.screen.hotkey.assert_not_called()

    def test_a_consent_banner_is_handled_twice_then_left_to_think(self):
        outcomes = [self._assess(self.CONSENT, i) for i in range(3)]
        self.assertEqual(outcomes, ["handled", "handled", "clear"])
        self.assertEqual(self.screen.hotkey.call_count, 2)

    def test_the_cap_is_per_obstacle_type(self):
        for i in range(2):
            self._assess(self.CONSENT, i)
        self.assertEqual(self._assess("example.com wants to use your camera", 2), "handled")

    def _escalate(self, decision_json):
        from types import SimpleNamespace
        from backend.services.agent_control_service import AgentControlService
        analyzer = MagicMock()
        analyzer.text_query.return_value = SimpleNamespace(success=True, description=decision_json)
        with patch.object(AgentControlService, "_get_thinking_model", staticmethod(lambda: "thinker")), \
             patch("backend.services.servo_controller.ServoController") as servo:
            outcome = self.svc._assess_obstacles(
                "A dialog asks: Are you sure you want to delete your account?", analyzer, self.screen, 0)
        analyzer.text_query.assert_called_once()
        return outcome, servo

    def test_an_escalation_click_on_a_banned_target_is_not_sent(self):
        self.svc._banned_targets = {"delete account button": "Delete account button"}
        self.svc._not_found_counts = {"delete account button": 3}
        outcome, servo = self._escalate(
            '{"action": "click", "target_description": "Delete account button", "reasoning": "confirm"}')
        self.assertEqual(outcome, "clear")
        servo.assert_not_called()
        self.screen.hotkey.assert_not_called()

    def test_an_allowed_escalation_hotkey_is_sent(self):
        outcome, _ = self._escalate('{"action": "hotkey", "keys": ["Escape"], "reasoning": "dismiss"}')
        self.assertEqual(outcome, "escalated")
        self.screen.hotkey.assert_called_once_with("Escape")


class TestFocusFirefox(unittest.TestCase):
    """The focus-firefox shortcut succeeds only when a Firefox window was
    found and windowactivate returned 0."""

    WINDOW = MagicMock(returncode=0, stdout="4194307\n", stderr="")

    def setUp(self):
        from backend.services.agent_control_service import AgentControlService
        self.svc = AgentControlService()
        self.screen = MagicMock(display=":99")
        sleep = patch("time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def _focus(self, *runs):
        with patch("subprocess.run", side_effect=list(runs)) as run:
            return self.svc._focus_firefox(self.screen), run

    def test_no_window_found_is_a_failure(self):
        result, run = self._focus(MagicMock(returncode=1, stdout="", stderr=""))
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "recipe:focus_firefox")
        self.assertEqual(result.steps[0].result["reason"], "no Firefox window")
        self.assertTrue(result.steps[0].failed)
        self.assertEqual(run.call_count, 1)

    def test_a_failed_activate_is_a_failure(self):
        result, _ = self._focus(self.WINDOW, MagicMock(returncode=1, stdout="", stderr="BadWindow"))
        self.assertFalse(result.success)
        self.assertEqual(result.steps[0].result["reason"], "BadWindow")

    def test_a_raised_error_is_a_failure(self):
        result, _ = self._focus(FileNotFoundError("xdotool not found"))
        self.assertFalse(result.success)
        self.assertIn("xdotool not found", result.steps[0].result["reason"])

    def test_a_focused_window_is_success(self):
        result, run = self._focus(self.WINDOW, MagicMock(returncode=0, stdout="", stderr=""))
        self.assertTrue(result.success)
        self.assertEqual(result.reason, "recipe:focus_firefox")
        self.assertEqual(run.call_args_list[1].args[0][:2], ["xdotool", "windowactivate"])
