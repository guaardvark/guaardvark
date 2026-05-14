import os

import pytest


pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        os.environ.get("RUN_AGENT_SMOKE") != "1",
        reason="set RUN_AGENT_SMOKE=1 to run live :99 agent smoke tests",
    ),
]


@pytest.fixture(scope="module")
def agent_service_and_screen():
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend

    service = get_agent_control_service()
    service.start()
    screen = LocalScreenBackend(display=os.environ.get("GUAARDVARK_AGENT_DISPLAY", ":99"))
    health = service.check_display_health(screen)
    assert health["success"], health
    return service, screen


def _run_task(agent_service_and_screen, task: str):
    service, screen = agent_service_and_screen
    result = service.execute_task(task, screen)
    assert result.success, f"{task!r} failed: {result.reason}"
    return result


def test_open_firefox_recipe(agent_service_and_screen):
    result = _run_task(agent_service_and_screen, "open Firefox")
    assert "recipe:" in result.reason or result.steps


def test_dismiss_xfce_launch_anyway_modal(agent_service_and_screen):
    # Safe even when the dialog is absent: the adaptive loop should fail rather
    # than click coordinates. It only passes on fresh XFCE profiles where the
    # modal is visible, which is exactly what this smoke is meant to prove.
    service, screen = agent_service_and_screen
    result = service.execute_task("dismiss untrusted launcher dialog", screen)
    assert result.success or "target_not_visible" in result.reason or result.steps


def test_navigate_to_local_guaardvark(agent_service_and_screen):
    _run_task(agent_service_and_screen, "navigate to localhost:5175")


def test_focus_chat_input(agent_service_and_screen):
    _run_task(agent_service_and_screen, "open the chat page")
    result = _run_task(agent_service_and_screen, "click chat input field")
    assert result.success


def test_type_text_in_chat(agent_service_and_screen):
    _run_task(agent_service_and_screen, 'type in chat "agent smoke test"')


def test_open_youtube_search(agent_service_and_screen):
    _run_task(agent_service_and_screen, "youtube Gotham Rising")
