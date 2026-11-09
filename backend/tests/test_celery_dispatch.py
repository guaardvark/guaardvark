"""Sending a Celery task fails within seconds when Redis is not there, says so
as TaskNotStarted, and the helpers and routes that send report it.

The dead-broker cases build their own GuaardvarkCelery with
apply_sender_bounds, pointed at redis://127.0.0.1:1/0 (nothing listens on
port 1, so every connect is refused) or at a local listener whose accept queue
is full (a connect that is never answered). Nothing reaches a real Redis.
"""

from __future__ import annotations

import socket
import sys
import threading
import time

import pytest

from backend.celery_dispatch import (
    CONNECT_TIMEOUT_S,
    PUBLISH_RETRY_POLICY,
    RESULT_RETRY_POLICY,
    GuaardvarkCelery,
    TaskNotStarted,
    apply_sender_bounds,
    not_started,
    queue_address,
    register_flask_handler,
)

REFUSED = "redis://127.0.0.1:1/0"


@pytest.fixture(autouse=True)
def _broker_from_the_test(monkeypatch):
    """Celery reads CELERY_BROKER_URL and CELERY_RESULT_BACKEND ahead of the
    broker an app is built with. With .env exported they named the live Redis,
    and dispatch_bound_test.noop reached it instead of the dead address."""
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("CELERY_RESULT_BACKEND", raising=False)


def _app(url: str) -> GuaardvarkCelery:
    app = GuaardvarkCelery("dispatch-bound-test", broker=url, backend=url, set_as_current=False)
    apply_sender_bounds(app.conf)
    return app


def _timed_send(app, cap: float = 30.0) -> dict:
    """send_task from a thread, so a regression shows as a failure, not a hang."""
    box: dict = {}

    def run():
        start = time.monotonic()
        try:
            app.send_task("dispatch_bound_test.noop")
        except BaseException as exc:  # noqa: BLE001 - the test inspects it
            box["error"] = exc
        box["elapsed"] = time.monotonic() - start

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(cap)
    assert not thread.is_alive(), f"send_task still blocked after {cap}s"
    return box


def test_the_backend_app_bounds_sending_and_keeps_workers_reconnecting():
    from backend.celery_app import celery

    conf = celery.conf
    assert isinstance(celery, GuaardvarkCelery)
    assert conf.broker_connection_max_retries is None
    assert conf.task_publish_retry_policy == PUBLISH_RETRY_POLICY
    assert conf.broker_connection_timeout == CONNECT_TIMEOUT_S
    assert conf.broker_transport_options["socket_connect_timeout"] == CONNECT_TIMEOUT_S
    assert conf.redis_socket_connect_timeout == CONNECT_TIMEOUT_S
    assert conf.result_backend_transport_options["retry_policy"] == RESULT_RETRY_POLICY
    # The settings the bound adds to keep what was there.
    assert conf.broker_transport_options["visibility_timeout"] == 172800


def test_a_send_with_redis_refused_fails_fast_and_says_so():
    box = _timed_send(_app("redis://:hunter2@127.0.0.1:1/0"))
    err = box.get("error")
    assert isinstance(err, TaskNotStarted), repr(err)
    # Celery's defaults spent 19 s here (20 result-channel retries, 1 s apart).
    assert box["elapsed"] < 3.0
    assert "dispatch_bound_test.noop was not started" in str(err)
    assert "127.0.0.1:1" in str(err) and "./start.sh" in str(err)
    assert "hunter2" not in str(err)
    assert err.code == "task_queue_unreachable"


def test_a_broker_only_send_fails_fast_too():
    app = _app(REFUSED)
    start = time.monotonic()
    with pytest.raises(TaskNotStarted):
        app.send_task("dispatch_bound_test.noop", ignore_result=True)
    assert time.monotonic() - start < 3.0


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="relies on Linux dropping connects to a full accept queue")
def test_a_send_to_an_address_that_never_answers_fails_within_the_bound():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(0)
    port = server.getsockname()[1]
    held = [server]
    try:
        for _ in range(4):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.setblocking(False)
            try:
                client.connect(("127.0.0.1", port))
            except BlockingIOError:
                pass
            held.append(client)
        time.sleep(0.2)
        box = _timed_send(_app(f"redis://127.0.0.1:{port}/0"), cap=60)
    finally:
        for sock in held:
            sock.close()
    assert isinstance(box.get("error"), TaskNotStarted), repr(box.get("error"))
    # Three connect attempts of CONNECT_TIMEOUT_S and one pause; with Celery's
    # defaults each of twenty attempts waited for the kernel's connect timeout.
    assert box["elapsed"] < 3 * CONNECT_TIMEOUT_S + 2


def test_a_worker_keeps_celerys_result_retries():
    from celery.signals import celeryd_init

    import backend.celery_app  # noqa: F401 - connects the worker receiver

    app = _app(REFUSED)
    celeryd_init.send(sender="test@host", instance=None, conf=app.conf, options={})
    assert "retry_policy" not in app.conf.result_backend_transport_options
    assert app.conf.task_publish_retry_policy == PUBLISH_RETRY_POLICY


def test_a_failing_task_marks_its_progress_entry(monkeypatch):
    """backend/celery_app.py's task_failure receiver is still connected after
    create_celery_app returns (Celery holds receivers weakly by default)."""
    import gc

    from celery.signals import task_failure

    import backend.celery_app  # noqa: F401 - connects the receiver
    from backend.utils import unified_progress_system as ups

    gc.collect()
    monkeypatch.setattr(ups.UnifiedProgressSystem, "_emit_event", lambda *a, **k: None)
    progress = ups.get_unified_progress()
    pid = progress.create_process(ups.ProcessType.FILE_GENERATION, "failing task")
    task_failure.send(sender=None, task_id="celery-id-x", exception=ValueError("boom"),
                      kwargs={"job_id": pid})
    event = progress.get_process(pid)
    assert event.status.value == "error" and "boom" in event.message


def test_the_result_channel_failure_is_reported_as_not_started():
    import redis

    app = _app(REFUSED)
    try:
        try:
            raise redis.exceptions.ConnectionError("Error 111 connecting to 127.0.0.1:1. Connection refused.")
        except redis.exceptions.ConnectionError as cause:
            raise RuntimeError("Retry limit exceeded while trying to reconnect") from cause
    except RuntimeError as exc:
        failure = not_started("x.task", app, exc)
    assert isinstance(failure, TaskNotStarted)
    assert "Connection refused" in failure.reason
    assert not_started("x.task", app, RuntimeError("unrelated")) is None
    assert not_started("x.task", app, ValueError("bad arguments")) is None


def test_celery_health_says_down_in_one_line():
    from backend.celery_dispatch import ping_worker

    start = time.monotonic()
    ok, line = ping_worker(_app(REFUSED))
    assert ok is False and time.monotonic() - start < 3.0
    assert line.startswith("down: Guaardvark's background queue (Redis at 127.0.0.1:1) is not reachable")
    assert "\n" not in line and "./start.sh" in line


def test_the_queue_address_never_carries_credentials():
    app = GuaardvarkCelery("address-test", broker="redis://user:secret@redis.example:6380/2", set_as_current=False)
    assert queue_address(app) == "redis.example:6380"


def test_a_route_that_does_not_catch_it_answers_503_with_the_reason():
    from flask import Flask

    from backend.models import db

    flask_app = Flask(__name__)
    flask_app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(flask_app)
    register_flask_handler(flask_app)

    @flask_app.post("/start")
    def start():
        raise TaskNotStarted("x.task", "localhost:6379", "Connection refused")

    resp = flask_app.test_client().post("/start")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["code"] == "task_queue_unreachable" and body["task"] == "x.task"
    assert "x.task was not started" in body["error"]


def test_try_dispatch_reports_why_an_agent_did_not_start():
    from backend.services.pipeline_service import PipelineService, dispatch_report

    class Service(PipelineService):
        task_namespace = "music_video"

        def dispatch_agent(self, row_id, agent_name):
            raise TaskNotStarted(f"music_video.run_{agent_name}", "localhost:6379", "Connection refused")

    warning = Service(None).try_dispatch(7, "clip_generator")
    assert warning.startswith("The clip generator was not started: Guaardvark's background queue")
    assert "localhost:6379" in warning and "./start.sh" in warning
    assert dispatch_report(warning) == {"dispatched": False, "warning": warning}
    assert dispatch_report(None) == {"dispatched": True}


@pytest.fixture
def production_client():
    from flask import Flask

    from backend.api.production_api import bp as production_bp
    from backend.models import db

    flask_app = Flask(__name__)
    flask_app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(flask_app)
    flask_app.register_blueprint(production_bp)
    register_flask_handler(flask_app)
    with flask_app.app_context():
        db.create_all()
        yield flask_app.test_client()
        db.session.remove()
        db.drop_all()


def test_a_new_production_says_its_screenwriter_did_not_start(production_client, monkeypatch):
    from backend.services.production_service import ProductionService

    def refused(self, prod_id, agent_name):
        raise TaskNotStarted(f"production.run_{agent_name}", "localhost:6379", "Connection refused")

    monkeypatch.setattr(ProductionService, "dispatch_agent", refused)
    resp = production_client.post("/api/production", json={"name": "X", "script_text": "x", "project_id": None})
    assert resp.status_code == 201  # the production exists; creating it again would duplicate it
    body = resp.get_json()
    assert body["current_stage"] == "screenwriting"
    assert body["dispatched"] is False
    assert "The screenwriter was not started" in body["warning"]


def test_a_queued_screenwriter_is_reported_as_dispatched(production_client, monkeypatch):
    from backend.services.production_service import ProductionService

    monkeypatch.setattr(ProductionService, "dispatch_agent", lambda self, prod_id, agent_name: None)
    body = production_client.post(
        "/api/production", json={"name": "X", "script_text": "x", "project_id": None},
    ).get_json()
    assert body["dispatched"] is True and "warning" not in body
