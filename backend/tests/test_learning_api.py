#!/usr/bin/env python3
"""Tests for the /api/agent-control/learn/* endpoints."""

import os
import sys
import json
import queue
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

try:
    from flask import Flask
    from backend.models import db, Demonstration
    from backend.api.agent_control_api import agent_control_bp
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    """Create a Flask app with in-memory SQLite for testing."""
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    app.register_blueprint(agent_control_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sample_demo(app):
    """Create a completed demonstration in the DB."""
    with app.app_context():
        d = Demonstration(
            name="Login Flow",
            description="Navigate to login page and sign in",
            tags=["login", "auth"],
            is_complete=True,
        )
        db.session.add(d)
        db.session.commit()
        # Return the id so tests can look it up fresh
        return d.id


# ---------------------------------------------------------------------------
# POST /learn/start
# ---------------------------------------------------------------------------

class TestLearnStart:
    def test_start_success(self, client):
        mock_service = MagicMock()
        mock_service.start_learning.return_value = {"success": True, "demonstration_id": 1}
        with patch(
            "backend.services.agent_control_service.get_agent_control_service",
            return_value=mock_service,
        ):
            resp = client.post(
                "/api/agent-control/learn/start",
                json={"name": "Test Demo", "description": "A test", "tags": ["t"]},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        mock_service.start_learning.assert_called_once_with(
            name="Test Demo", description="A test", tags=["t"]
        )

    def test_start_conflict(self, client):
        mock_service = MagicMock()
        mock_service.start_learning.return_value = {
            "success": False,
            "error": "Already learning",
        }
        with patch(
            "backend.services.agent_control_service.get_agent_control_service",
            return_value=mock_service,
        ):
            resp = client.post("/api/agent-control/learn/start", json={})
        assert resp.status_code == 409

    def test_start_no_body(self, client):
        """Start with empty JSON body uses defaults."""
        mock_service = MagicMock()
        mock_service.start_learning.return_value = {"success": True, "demonstration_id": 2}
        with patch(
            "backend.services.agent_control_service.get_agent_control_service",
            return_value=mock_service,
        ):
            resp = client.post("/api/agent-control/learn/start", json={})
        assert resp.status_code == 200
        mock_service.start_learning.assert_called_once_with(
            name=None, description="", tags=None
        )


# ---------------------------------------------------------------------------
# POST /learn/stop
# ---------------------------------------------------------------------------

class TestLearnStop:
    def test_stop_success(self, client):
        mock_service = MagicMock()
        mock_service.stop_learning.return_value = {"success": True, "demonstration_id": 1}
        with patch(
            "backend.services.agent_control_service.get_agent_control_service",
            return_value=mock_service,
        ):
            resp = client.post("/api/agent-control/learn/stop")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_stop_not_learning(self, client):
        mock_service = MagicMock()
        mock_service.stop_learning.return_value = {
            "success": False,
            "error": "Not currently learning",
        }
        with patch(
            "backend.services.agent_control_service.get_agent_control_service",
            return_value=mock_service,
        ):
            resp = client.post("/api/agent-control/learn/stop")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /learn/status
# ---------------------------------------------------------------------------

class TestLearnStatus:
    def test_status_idle(self, client):
        mock_service = MagicMock()
        mock_service.is_learning = False
        mock_service._current_demonstration_id = None
        with patch(
            "backend.services.agent_control_service.get_agent_control_service",
            return_value=mock_service,
        ):
            resp = client.get("/api/agent-control/learn/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["learning"] is False
        assert data["demonstration_id"] is None

    def test_status_active(self, client):
        mock_service = MagicMock()
        mock_service.is_learning = True
        mock_service._current_demonstration_id = 42
        with patch(
            "backend.services.agent_control_service.get_agent_control_service",
            return_value=mock_service,
        ):
            resp = client.get("/api/agent-control/learn/status")
        data = resp.get_json()
        assert data["learning"] is True
        assert data["demonstration_id"] == 42


# ---------------------------------------------------------------------------
# GET /learn/demonstrations
# ---------------------------------------------------------------------------

class TestListDemonstrations:
    def test_list_empty(self, client):
        resp = client.get("/api/agent-control/learn/demonstrations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["demonstrations"] == []

    def test_list_returns_complete_only(self, client, app, sample_demo):
        # Add an incomplete demo — should NOT be listed
        with app.app_context():
            d = Demonstration(
                description="Incomplete",
                is_complete=False,
            )
            db.session.add(d)
            db.session.commit()

        resp = client.get("/api/agent-control/learn/demonstrations")
        data = resp.get_json()
        assert len(data["demonstrations"]) == 1
        assert data["demonstrations"][0]["name"] == "Login Flow"


# ---------------------------------------------------------------------------
# GET /learn/demonstrations/<id>
# ---------------------------------------------------------------------------

class TestGetDemonstration:
    def test_get_existing(self, client, sample_demo):
        resp = client.get(f"/api/agent-control/learn/demonstrations/{sample_demo}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["demonstration"]["name"] == "Login Flow"
        assert data["demonstration"]["tags"] == ["login", "auth"]

    def test_get_not_found(self, client):
        resp = client.get("/api/agent-control/learn/demonstrations/99999")
        assert resp.status_code == 404
        assert resp.get_json()["success"] is False


# ---------------------------------------------------------------------------
# DELETE /learn/demonstrations/<id>
# ---------------------------------------------------------------------------

class TestDeleteDemonstration:
    def test_delete_existing(self, client, app, sample_demo):
        resp = client.delete(f"/api/agent-control/learn/demonstrations/{sample_demo}")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        # Confirm gone
        with app.app_context():
            assert db.session.get(Demonstration, sample_demo) is None

    def test_delete_not_found(self, client):
        resp = client.delete("/api/agent-control/learn/demonstrations/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /learn/demonstrations/<id>
# ---------------------------------------------------------------------------

class TestUpdateDemonstration:
    def test_update_name(self, client, app, sample_demo):
        resp = client.patch(
            f"/api/agent-control/learn/demonstrations/{sample_demo}",
            json={"name": "Updated Name"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["demonstration"]["name"] == "Updated Name"
        # description unchanged
        assert data["demonstration"]["description"] == "Navigate to login page and sign in"

    def test_update_tags(self, client, app, sample_demo):
        resp = client.patch(
            f"/api/agent-control/learn/demonstrations/{sample_demo}",
            json={"tags": ["new-tag"]},
        )
        data = resp.get_json()
        assert data["demonstration"]["tags"] == ["new-tag"]

    def test_update_not_found(self, client):
        resp = client.patch(
            "/api/agent-control/learn/demonstrations/99999",
            json={"name": "x"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /learn/demonstrations/<id>/attempt
# ---------------------------------------------------------------------------

class TestAttemptDemonstration:
    def test_attempt_success(self, client, sample_demo):
        mock_service = MagicMock()
        mock_service.attempt_demonstration.return_value = {
            "success": True,
            "attempt_id": 99,
        }
        with patch(
            "backend.services.agent_control_service.get_agent_control_service",
            return_value=mock_service,
        ):
            resp = client.post(
                f"/api/agent-control/learn/demonstrations/{sample_demo}/attempt"
            )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        mock_service.attempt_demonstration.assert_called_once_with(sample_demo)

    def test_attempt_conflict(self, client, sample_demo):
        mock_service = MagicMock()
        mock_service.attempt_demonstration.return_value = {
            "success": False,
            "error": "Agent busy",
        }
        with patch(
            "backend.services.agent_control_service.get_agent_control_service",
            return_value=mock_service,
        ):
            resp = client.post(
                f"/api/agent-control/learn/demonstrations/{sample_demo}/attempt"
            )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /learn/demonstrations/<id>/feedback
# ---------------------------------------------------------------------------

class TestDemonstrationFeedback:
    def test_feedback_success(self, client, app, sample_demo):
        resp = client.post(
            f"/api/agent-control/learn/demonstrations/{sample_demo}/feedback",
            json={"success": True},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["demonstration"]["success_count"] == 1
        assert data["demonstration"]["attempt_count"] == 1

    def test_feedback_failure_resets_streak(self, client, app, sample_demo):
        # First give two successes
        client.post(
            f"/api/agent-control/learn/demonstrations/{sample_demo}/feedback",
            json={"success": True},
        )
        client.post(
            f"/api/agent-control/learn/demonstrations/{sample_demo}/feedback",
            json={"success": True},
        )
        # Then a failure resets success_count to 0
        resp = client.post(
            f"/api/agent-control/learn/demonstrations/{sample_demo}/feedback",
            json={"success": False},
        )
        data = resp.get_json()
        assert data["demonstration"]["success_count"] == 0
        assert data["demonstration"]["attempt_count"] == 3

    def test_feedback_not_found(self, client):
        resp = client.post(
            "/api/agent-control/learn/demonstrations/99999/feedback",
            json={"success": True},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /learn/answer
# ---------------------------------------------------------------------------

class TestLearnAnswer:
    def test_answer_enqueued(self, client):
        mock_queue = MagicMock(spec=queue.Queue)
        mock_service = MagicMock()
        mock_service._learning_answer_queue = mock_queue
        with patch(
            "backend.services.agent_control_service.get_agent_control_service",
            return_value=mock_service,
        ):
            resp = client.post(
                "/api/agent-control/learn/answer",
                json={"answer": "yes, the blue button"},
            )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        mock_queue.put.assert_called_once_with({"answer": "yes, the blue button"})
