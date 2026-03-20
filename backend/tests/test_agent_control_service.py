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
        self.assertEqual(config.max_iterations, 50)
        self.assertEqual(config.verify_actions, True)
        self.assertEqual(config.grid_cols, 8)
        self.assertEqual(config.grid_rows, 8)
        self.assertEqual(config.vision_model, "moondream")
        self.assertEqual(config.max_consecutive_failures, 5)


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
        self.assertIn("grid cell", prompt.lower())
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
