import os
import shutil
import subprocess
import time

import pytest

try:
    from playwright.sync_api import sync_playwright
except Exception:
    pytest.skip("playwright not installed", allow_module_level=True)

if not os.getenv("RUN_PLAYWRIGHT_TESTS"):
    pytest.skip("Playwright tests disabled", allow_module_level=True)
import requests

FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../frontend")
)


@pytest.fixture(scope="session")
def vite_server():
    if shutil.which("npm") is None:
        pytest.skip("npm not installed", allow_module_level=True)
    if not os.path.isdir(os.path.join(FRONTEND_DIR, "node_modules")):
        pytest.skip("frontend dependencies not installed", allow_module_level=True)
    proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
        cwd=FRONTEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for _ in range(30):
        try:
            r = requests.get("http://127.0.0.1:5173")
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        proc.terminate()
        raise RuntimeError("Vite server failed to start")
    yield
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"Playwright browser not available: {e}")
        yield browser
        browser.close()


def test_chat_page_load(browser, vite_server):
    context = browser.new_context()
    page = context.new_page()

    def intercept_chat_history(route):
        route.fulfill(
            status=200,
            body='{"messages": []}',
            headers={"Content-Type": "application/json"},
        )

    page.route("**/api/chat/main_chat_session**", intercept_chat_history)

    page.goto("http://127.0.0.1:5173/chat")
    page.wait_for_selector("text=LLM Chat")
    page.wait_for_selector("textarea")
    context.close()


def test_chat_page_handles_bad_history(browser, vite_server):
    context = browser.new_context()
    page = context.new_page()

    def intercept_bad_history(route):
        route.fulfill(
            status=200,
            body='{"foo": "bar"}',
            headers={"Content-Type": "application/json"},
        )

    page.route("**/api/chat/main_chat_session**", intercept_bad_history)

    page.goto("http://127.0.0.1:5173/chat")
    page.wait_for_selector("text=LLM Chat")
    page.wait_for_selector("textarea")
    context.close()


def test_dashboard_drag(browser, vite_server):
    context = browser.new_context()
    page = context.new_page()

    def handle_state(route):
        if route.request.method == "GET":
            route.fulfill(
                status=200, body="[]", headers={"Content-Type": "application/json"}
            )
        else:
            route.fulfill(
                status=200, body="{}", headers={"Content-Type": "application/json"}
            )

    page.route("**/api/state/layout", handle_state)

    page.goto("http://127.0.0.1:5173/dashboard")
    header = page.locator("text=Project Manager").first
    header.wait_for()
    box_before = header.bounding_box()
    page.mouse.move(box_before["x"] + 5, box_before["y"] + 5)
    page.mouse.down()
    page.mouse.move(box_before["x"] + 150, box_before["y"] + 5, steps=10)
    page.mouse.up()
    box_after = header.bounding_box()
    assert box_after["x"] != box_before["x"]
    context.close()
