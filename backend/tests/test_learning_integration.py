"""Integration test for the full learning pipeline: record -> save -> attempt."""
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image

try:
    from flask import Flask
    from backend.models import db, Demonstration, DemoStep
    from backend.services.apprentice_engine import ApprenticeEngine, AttemptResult
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


class TestLearningIntegration:
    def test_demonstration_round_trip(self, app):
        """Create a demo with steps, then verify ApprenticeEngine can execute them."""
        with app.app_context():
            demo = Demonstration(
                name="Integration Test",
                description="Click login, type username, submit",
                context_url="http://localhost/login",
                tags=["test"],
            )
            db.session.add(demo)
            db.session.commit()

            steps_data = [
                {"step_index": 0, "action_type": "click", "target_description": "username field",
                 "element_context": "first input in form", "coordinates_x": 640, "coordinates_y": 300,
                 "precondition": "Login form visible", "variability": False},
                {"step_index": 1, "action_type": "type", "target_description": "username field",
                 "element_context": "first input in form", "coordinates_x": 640, "coordinates_y": 300,
                 "text": "admin", "precondition": "Username field focused", "variability": True},
                {"step_index": 2, "action_type": "click", "target_description": "Login button",
                 "element_context": "below password field", "coordinates_x": 640, "coordinates_y": 450,
                 "precondition": "Form filled", "variability": False},
            ]
            for sd in steps_data:
                step = DemoStep(demonstration_id=demo.id, **sd)
                db.session.add(step)
            db.session.commit()

            loaded = db.session.get(Demonstration, demo.id)
            assert loaded.name == "Integration Test"
            loaded_steps = [s.to_dict() for s in loaded.steps]
            assert len(loaded_steps) == 3
            assert loaded_steps[0]["action_type"] == "click"
            assert loaded_steps[1]["action_type"] == "type"
            assert loaded_steps[1]["variability"] is True

    def test_apprentice_executes_demo_steps(self):
        """Verify ApprenticeEngine can run through steps using mocked screen/servo."""
        mock_screen = MagicMock()
        img = Image.new("RGB", (1280, 720), "white")
        mock_screen.capture.return_value = (img, (640, 360))
        mock_screen.type_text.return_value = {"success": True}
        mock_screen.hotkey.return_value = {"success": True}

        mock_analyzer = MagicMock()
        vision_result = MagicMock()
        vision_result.success = True
        vision_result.description = '{"matches": true, "description": "screen matches"}'
        mock_analyzer.analyze.return_value = vision_result
        mock_analyzer.text_query.return_value = vision_result

        mock_servo = MagicMock()
        mock_servo.click_target.return_value = {
            "success": True, "verified": True, "x": 640, "y": 400,
            "corrections": 0, "attempt": 1, "time_ms": 300,
        }

        engine = ApprenticeEngine(
            screen=mock_screen,
            analyzer=mock_analyzer,
            servo=mock_servo,
        )

        steps = [
            {"step_index": 0, "action_type": "click", "target_description": "username field",
             "element_context": "first input", "precondition": "Login form visible",
             "variability": False, "text": None, "keys": None, "screenshot_after": None},
            {"step_index": 1, "action_type": "type", "target_description": "username field",
             "element_context": "first input", "precondition": "Field focused",
             "variability": False, "text": "admin", "keys": None, "screenshot_after": None},
            {"step_index": 2, "action_type": "hotkey", "target_description": "submit",
             "element_context": "", "precondition": "",
             "variability": False, "text": None, "keys": "Return", "screenshot_after": None},
        ]

        result = engine.execute(steps=steps, autonomy_level="autonomous")
        assert result.success is True
        assert result.steps_completed == 3
        mock_servo.click_target.assert_called_once()
        mock_screen.type_text.assert_called_once_with("admin")
        mock_screen.hotkey.assert_called_once_with("Return")

    def test_graduation_flow(self, app):
        """Verify graduation logic updates autonomy_level correctly."""
        with app.app_context():
            demo = Demonstration(
                description="Graduation test",
                autonomy_level="guided",
                success_count=0,
            )
            db.session.add(demo)
            db.session.commit()

            for _ in range(3):
                demo.success_count += 1
            assert ApprenticeEngine._should_promote("guided", demo.success_count)
            demo.autonomy_level = ApprenticeEngine._promote(demo.autonomy_level)
            demo.success_count = 0
            db.session.commit()
            assert demo.autonomy_level == "supervised"

            demo.autonomy_level = ApprenticeEngine._demote(demo.autonomy_level)
            db.session.commit()
            assert demo.autonomy_level == "guided"
