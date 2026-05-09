"""
Celery routing regression tests — pins the split-brain fix.

Before: Flask created its own Celery via make_celery() with empty task_routes.
Worker had a separate instance with proper routing. .delay() from Flask
published to a queue the worker wasn't subscribed to, producing ghost task_ids.

After: Both processes share the singleton from backend.celery_app.
"""
import sys
import importlib

def test_api_task_routes_to_default_queue():
    """Tasks queued from Flask MUST land in the same queue workers consume.

    Before: dual Celery instances meant Flask's .delay() messages went to
    a queue the worker wasn't listening to, producing ghost task_ids.
    """
    from backend.celery_app import celery
    # Triggers task registration via shared_task decorators
    try:
        from backend.tasks.social_outreach_tasks import engage_with_subreddit  # noqa
    except ImportError:
        # social_outreach_tasks may not exist yet; skip if missing
        import pytest
        pytest.skip("social_outreach_tasks not available")
    
    route = celery.amqp.router.route(
        {}, "social_outreach.engage_with_subreddit", args=["test"], kwargs={}
    )
    queue = route.get("queue")
    queue_name = queue.name if hasattr(queue, 'name') else str(queue)
    assert queue_name == "default", \
        f"Task routed to {queue_name!r}, worker subscribes to 'default'"


def test_flask_and_worker_share_celery_instance():
    """Flask must NOT call make_celery() — split-brain bug.
    
    The worker uses backend.celery_app.celery. If Flask creates its own instance
    via make_celery(), tasks queued from Flask won't reach the worker.
    """
    import ast
    from pathlib import Path
    
    # Parse app.py and check that make_celery() is not called anywhere
    # in create_app() or _initialize_app_components()
    app_py = Path(__file__).parent.parent / "app.py"
    tree = ast.parse(app_py.read_text())
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("create_app", "_initialize_app_components"):
            # Look for any call to make_celery()
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id == "make_celery":
                        raise AssertionError(
                            f"{node.name}() calls make_celery() at line {child.lineno} — "
                            "Flask will use its own Celery instance instead of the shared "
                            "worker singleton from backend.celery_app, causing ghost task_ids"
                        )


def test_shared_task_resolves_to_configured_celery_app():
    """@shared_task decorators MUST bind to the configured backend.celery_app.celery,
    not a default empty Celery() that the lib creates on first lookup.

    Without celery.set_default() in celery_app.py, @shared_task in modules
    imported before celery_app falls through to an unconfigured default,
    and .delay() messages route to the literal 'celery' queue instead of
    the routed 'default' queue — producing ghost task_ids that the worker
    never receives.
    """
    # Force fresh import order so we catch the bug if it returns
    for mod in ("backend.tasks.social_outreach_tasks", "backend.celery_app"):
        if mod in sys.modules:
            del sys.modules[mod]

    # Import task FIRST (worst case — shared_task binds before celery_app loads)
    from backend.tasks.social_outreach_tasks import engage_with_subreddit
    # Then import celery_app
    from backend.celery_app import celery as configured

    assert engage_with_subreddit.app is configured, (
        f"@shared_task bound to a different Celery instance "
        f"(id={id(engage_with_subreddit.app)}) than the configured one "
        f"(id={id(configured)}). celery.set_default() in celery_app.py "
        f"is missing or being undone."
    )


def test_no_circular_import_in_celery_app(caplog):
    """celery_app must import cleanly without partially-initialized warnings.
    
    The video_render_tasks import was creating a cycle through video_overlay_api
    → celery_app, suppressed by a try/except but still logged at every startup.
    """
    import logging
    
    # Force a fresh import to catch circular import issues
    modules_to_clear = [
        "backend.celery_app",
        "backend.tasks.video_render_tasks",
    ]
    for mod in modules_to_clear:
        if mod in sys.modules:
            del sys.modules[mod]
    
    # Capture logs during import
    with caplog.at_level(logging.WARNING):
        importlib.import_module("backend.celery_app")
    
    # Check for circular import warnings
    for record in caplog.records:
        if "partially initialized" in record.message.lower() or "circular import" in record.message.lower():
            raise AssertionError(
                f"Circular import detected in celery_app:\n{record.message}"
            )
