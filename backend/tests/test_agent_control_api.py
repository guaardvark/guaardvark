#!/usr/bin/env python3

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


class TestAgentControlAPI(unittest.TestCase):

    def test_blueprint_exists(self):
        from backend.api.agent_control_api import agent_control_bp
        self.assertEqual(agent_control_bp.name, "agent_control")
        self.assertEqual(agent_control_bp.url_prefix, "/api/agent-control")

    def test_status_endpoint(self):
        from backend.api.agent_control_api import agent_control_bp
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(agent_control_bp)

        with app.test_client() as client:
            resp = client.get("/api/agent-control/status")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["success"])
            self.assertIn("active", data["status"])

    def test_kill_endpoint(self):
        from backend.api.agent_control_api import agent_control_bp
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(agent_control_bp)

        with app.test_client() as client:
            resp = client.post("/api/agent-control/kill")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["success"])


if __name__ == "__main__":
    unittest.main()
