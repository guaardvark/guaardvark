"""Tests for Demonstration and DemoStep models."""
import pytest
from datetime import datetime

try:
    from flask import Flask
    from backend.models import db, Demonstration, DemoStep
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def demo(app):
    """Create a test demonstration."""
    with app.app_context():
        d = Demonstration(
            name="Login to WordPress",
            description="Navigate to wp-admin and log in",
            context_url="http://localhost/wp-admin",
            context_app="wordpress",
            tags=["login", "wordpress"],
        )
        db.session.add(d)
        db.session.commit()
        yield d


class TestDemonstrationModel:
    def test_create_demonstration(self, app, demo):
        with app.app_context():
            d = db.session.get(Demonstration, demo.id)
            assert d is not None
            assert d.name == "Login to WordPress"
            assert d.description == "Navigate to wp-admin and log in"
            assert d.context_url == "http://localhost/wp-admin"
            assert d.context_app == "wordpress"
            assert d.tags == ["login", "wordpress"]
            assert d.autonomy_level == "guided"
            assert d.success_count == 0
            assert d.attempt_count == 0
            assert d.parent_demonstration_id is None
            assert d.created_at is not None

    def test_demonstration_to_dict(self, app, demo):
        with app.app_context():
            d = db.session.get(Demonstration, demo.id)
            result = d.to_dict()
            assert result["name"] == "Login to WordPress"
            assert result["autonomy_level"] == "guided"
            assert "steps" in result
            assert isinstance(result["steps"], list)

    def test_demonstration_unnamed(self, app):
        with app.app_context():
            d = Demonstration(description="Quick test action")
            db.session.add(d)
            db.session.commit()
            assert d.name is None
            assert d.id is not None

    def test_parent_child_link(self, app, demo):
        with app.app_context():
            child = Demonstration(
                description="Agent attempt of Login to WordPress",
                parent_demonstration_id=demo.id,
            )
            db.session.add(child)
            db.session.commit()
            assert child.parent_demonstration_id == demo.id
            parent = db.session.get(Demonstration, demo.id)
            assert len(list(parent.attempts)) == 1


class TestDemoStepModel:
    def test_create_step(self, app, demo):
        with app.app_context():
            step = DemoStep(
                demonstration_id=demo.id,
                step_index=0,
                action_type="click",
                target_description="the blue Login button below the password field",
                element_context="inside a white login card, centered on page",
                coordinates_x=640,
                coordinates_y=400,
                intent="Submit login credentials",
                precondition="Login form visible with filled email and password fields",
                variability=False,
                screenshot_before="data/training/demonstrations/test/step_0_before.jpg",
                screenshot_after="data/training/demonstrations/test/step_0_after.jpg",
            )
            db.session.add(step)
            db.session.commit()
            assert step.id is not None
            assert step.action_type == "click"
            assert step.variability is False

    def test_step_with_text(self, app, demo):
        with app.app_context():
            step = DemoStep(
                demonstration_id=demo.id,
                step_index=1,
                action_type="type",
                target_description="the username input field",
                element_context="first field in login form",
                coordinates_x=640,
                coordinates_y=350,
                text="admin@example.com",
                intent="Enter username",
                precondition="Login form visible, username field focused",
                variability=True,
            )
            db.session.add(step)
            db.session.commit()
            assert step.text == "admin@example.com"
            assert step.variability is True

    def test_step_ordering(self, app, demo):
        with app.app_context():
            for i in range(3):
                step = DemoStep(
                    demonstration_id=demo.id,
                    step_index=i,
                    action_type="click",
                    target_description=f"element {i}",
                    element_context=f"context {i}",
                    coordinates_x=100 * i,
                    coordinates_y=100,
                    intent=f"step {i}",
                    precondition=f"precondition {i}",
                )
                db.session.add(step)
            db.session.commit()
            d = db.session.get(Demonstration, demo.id)
            steps = list(d.steps)
            assert len(steps) == 3
            assert steps[0].step_index == 0
            assert steps[2].step_index == 2

    def test_step_to_dict(self, app, demo):
        with app.app_context():
            step = DemoStep(
                demonstration_id=demo.id,
                step_index=0,
                action_type="hotkey",
                target_description="keyboard shortcut",
                element_context="any context",
                coordinates_x=0,
                coordinates_y=0,
                keys="ctrl+l",
                intent="Open address bar",
                precondition="Browser window visible",
            )
            db.session.add(step)
            db.session.commit()
            result = step.to_dict()
            assert result["action_type"] == "hotkey"
            assert result["keys"] == "ctrl+l"
            assert "demonstration_id" not in result
