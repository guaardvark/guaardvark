from unittest.mock import patch, MagicMock
from datetime import datetime
import pytest


@pytest.fixture
def app():
    from flask import Flask, jsonify
    from backend.models import db

    a = Flask(__name__)
    a.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "CLUSTER_ENABLED": False,
    })
    db.init_app(a)

    @a.route("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @a.route("/api/chat/unified", methods=["POST"])
    def chat():
        return jsonify({"message": "local"}), 200

    with a.app_context():
        db.create_all()
        # Register the middleware under test
        from backend.middleware.cluster_proxy_middleware import cluster_proxy_before_request
        a.before_request(cluster_proxy_before_request)
        yield a
        db.session.remove()
        db.drop_all()


def test_middleware_noop_when_cluster_disabled(app):
    app.config["CLUSTER_ENABLED"] = False
    client = app.test_client()
    r = client.get("/api/health")
    assert r.status_code == 200  # pass-through


def test_middleware_noop_for_unclassified_path(app, monkeypatch):
    app.config["CLUSTER_ENABLED"] = True
    client = app.test_client()
    r = client.get("/api/health")
    # Health is in ALWAYS_LOCAL; middleware returns None; handler serves it
    assert r.status_code == 200


def test_middleware_force_local_when_hops_maxed(app):
    """Request with X-Guaardvark-Hops >= 2 must fall through to local handler,
    not attempt proxy."""
    app.config["CLUSTER_ENABLED"] = True
    client = app.test_client()
    r = client.post("/api/chat/unified", json={"message": "hi"},
                    headers={"X-Guaardvark-Hops": "2"})
    # Should NOT be a 502 from failed proxy; should be handler's own response
    # (could be 200, 4xx, or 5xx depending on backend state — just not 502)
    assert r.status_code != 502


def test_middleware_forwards_workload_when_primary_is_remote(app, monkeypatch):
    from backend.services.cluster_routing import (
        RoutingTable, WorkloadRoute, get_routing_store,
    )
    from backend.models import db, InterconnectorNode

    app.config["CLUSTER_ENABLED"] = True
    monkeypatch.setenv("CLUSTER_NODE_ID", "mw-me")

    with app.app_context():
        db.session.add(InterconnectorNode(node_id="mw-remote", node_name="mw-remote",
                                          node_mode="client",
                                          host="192.168.1.99", port=5002,
                                          online=True))
        db.session.commit()

    r = WorkloadRoute(workload="llm_chat", mode="singular", primary="mw-remote",
                      fallback=[], workers=[], required_services=["ollama"],
                      min_vram_mb=4096, cpu_acceptable=False)
    t = RoutingTable(routes={"llm_chat": r}, computed_at=datetime.utcnow(),
                     computed_by="mw-remote", node_count=2, fleet_hash="x")
    get_routing_store().set(t, persist=False)

    fake_resp = MagicMock(status_code=202)
    fake_resp.iter_content = lambda chunk_size: iter([b'{"forwarded":true}'])
    fake_resp.headers = {"Content-Type": "application/json"}

    with patch("backend.services.cluster_proxy.HttpProxyForwarder.forward") as fwd:
        from flask import Response
        fwd.return_value = Response('{"forwarded":true}', status=202,
                                    mimetype="application/json")
        client = app.test_client()
        resp = client.post("/api/chat/unified", json={"message": "hi"})
        assert fwd.called, "middleware should have called forwarder"


def test_middleware_falls_back_to_local_when_forward_fails(app, monkeypatch):
    """When forwarder throws ConnectionError, middleware iterates to next
    target, then returns None (local handling)."""
    import requests as _rq
    from backend.services.cluster_routing import (
        RoutingTable, WorkloadRoute, get_routing_store,
    )
    from backend.models import db, InterconnectorNode

    app.config["CLUSTER_ENABLED"] = True
    monkeypatch.setenv("CLUSTER_NODE_ID", "fb-me")

    with app.app_context():
        db.session.add(InterconnectorNode(node_id="fb-dead", node_name="fb-dead",
                                          node_mode="client",
                                          host="192.168.99.99", port=5002,
                                          online=True))
        db.session.commit()

    r = WorkloadRoute(workload="llm_chat", mode="singular", primary="fb-dead",
                      fallback=[], workers=[], required_services=["ollama"],
                      min_vram_mb=4096, cpu_acceptable=False)
    t = RoutingTable(routes={"llm_chat": r}, computed_at=datetime.utcnow(),
                     computed_by="fb-dead", node_count=1, fleet_hash="x")
    get_routing_store().set(t, persist=False)

    with patch("backend.services.cluster_proxy.HttpProxyForwarder.forward",
               side_effect=_rq.ConnectionError("refused")):
        client = app.test_client()
        resp = client.post("/api/chat/unified", json={"message": "hi"})
        # Proxy failed; middleware returned None; local handler ran.
        # The test only verifies we didn't 502 from the proxy exception.
        assert resp.status_code != 502


def test_middleware_does_not_crash_on_error(app, monkeypatch):
    """Outer try/except in the middleware should swallow any unexpected
    exception (imports, DB hiccup, etc.) and let the request proceed locally."""
    app.config["CLUSTER_ENABLED"] = True
    client = app.test_client()
    # Force an exception inside classify by monkeypatching
    with patch("backend.services.cluster_proxy.WorkloadClassifier.classify",
               side_effect=RuntimeError("boom")):
        r = client.get("/api/health")
        # Middleware caught the error, fell through to local handler
        assert r.status_code == 200
