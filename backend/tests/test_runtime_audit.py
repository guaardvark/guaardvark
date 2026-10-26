"""Runtime-liveness layer tests (Phase 1 + Phase 3/B5).

No @requires_llm, no live services. Uses an in-memory sqlite app so the
flush()/prune paths exercise the portable read-modify-write upsert fallback.
"""

import pytest

try:
    from flask import Flask
    from backend.models import db, SymbolHit
    from backend.services import execution_context_tracker as ect
    from backend.services.execution_context_tracker import (
        ExecutionContextTracker,
        MODE_CELERY_TASK,
        MODE_FLASK_ROUTE,
    )
    from backend.tasks.runtime_audit_tasks import create_runtime_audit_tasks
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


# (a) buffer aggregation -------------------------------------------------------
def test_record_hit_aggregates_count_and_ors_mode_flags():
    tracker = ExecutionContextTracker()
    tracker.record_hit("task:foo", "task", "foo", "mod", mode_bit=1)
    tracker.record_hit("task:foo", "task", "foo", "mod", mode_bit=4)

    entry = tracker._buffer["task:foo"]
    assert entry["count"] == 2
    assert entry["mode_flags"] == (1 | 4)


# (b) idempotent upsert --------------------------------------------------------
def test_flush_upserts_idempotently(app):
    with app.app_context():
        tracker = ExecutionContextTracker()
        tracker.record_hit("task:bar", "task", "bar", "mod", mode_bit=MODE_CELERY_TASK)
        tracker.record_hit("task:bar", "task", "bar", "mod", mode_bit=MODE_CELERY_TASK)
        assert tracker.flush() == 1

        tracker.record_hit("task:bar", "task", "bar", "mod", mode_bit=2)
        tracker.flush()

        rows = SymbolHit.query.all()
        assert len(rows) == 1  # one row, not two
        row = rows[0]
        assert row.symbol_id == "task:bar"
        assert row.hit_count == 3            # 2 + 1, summed across flushes
        assert row.mode_flags == (1 | 2)     # ORed across flushes


def test_flush_empty_buffer_is_noop(app):
    with app.app_context():
        tracker = ExecutionContextTracker()
        assert tracker.flush() == 0
        assert SymbolHit.query.count() == 0


# (c) prune keeps reachable-but-cold ------------------------------------------
def test_prune_deletes_stale_nonreachable_keeps_reachable(app):
    import datetime as _dt

    with app.app_context():
        old = _dt.datetime.now() - _dt.timedelta(days=200)
        fresh = _dt.datetime.now()

        db.session.add(SymbolHit(
            symbol_id="task:stale_cold", symbol_kind="task", display_name="stale_cold",
            module="m", mode_flags=1, hit_count=1, last_fired_at=old,
            static_reachability=False,
        ))
        db.session.add(SymbolHit(
            symbol_id="task:stale_null", symbol_kind="task", display_name="stale_null",
            module="m", mode_flags=1, hit_count=1, last_fired_at=old,
            static_reachability=None,
        ))
        db.session.add(SymbolHit(
            symbol_id="task:stale_reachable", symbol_kind="task", display_name="stale_reachable",
            module="m", mode_flags=1, hit_count=1, last_fired_at=old,
            static_reachability=True,
        ))
        db.session.add(SymbolHit(
            symbol_id="task:fresh", symbol_kind="task", display_name="fresh",
            module="m", mode_flags=1, hit_count=1, last_fired_at=fresh,
            static_reachability=False,
        ))
        db.session.commit()

        from celery import Celery
        capp = Celery("test_runtime_audit")
        create_runtime_audit_tasks(capp)
        prune = capp.tasks["runtime_audit.prune_old_hits"]

        result = prune.run()
        assert result["status"] == "ok"
        assert result["deleted"] == 2

        remaining = {r.symbol_id for r in SymbolHit.query.all()}
        assert remaining == {"task:stale_reachable", "task:fresh"}


# (d) tracker never raises if the DB is unavailable ----------------------------
def test_tracker_swallows_db_errors(app, monkeypatch):
    with app.app_context():
        tracker = ExecutionContextTracker()
        tracker.record_hit("task:boom", "task", "boom", "mod", mode_bit=1)

        def _boom(*a, **k):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(db.session, "execute", _boom)
        monkeypatch.setattr(db.session, "get", _boom)
        monkeypatch.setattr(db.session, "add", _boom)

        assert tracker.flush() == 0
        assert "task:boom" in tracker._buffer


def test_record_hit_never_raises_on_bad_internal_state():
    tracker = ExecutionContextTracker()
    tracker._lock = None
    tracker.record_hit("task:x", "task", "x", "m", mode_bit=1)  # no exception


# ============================================================================
# B5: reconcile_and_emit — static_reachability set, liveness emitted into cache,
#     ONLY new HIGH dispatchable findings dispatched (NEVER a liveness finding).
# ============================================================================

def test_reconcile_and_emit_sets_reachability_emits_consensus_dispatches_only_high(
    app, monkeypatch, tmp_path
):
    import json
    import datetime as _dt
    from backend.tasks import runtime_audit_tasks as rat
    from backend.services.system_mapper import store

    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path / "storage"))
    with app.app_context():
        # --- seed SymbolHit rows ---------------------------------------------
        now = _dt.datetime.now()
        old = now - _dt.timedelta(days=120)
        # reachable module (matches the fake map's node_meta) but fired long ago
        # -> will become static_reachability True AND a RUNTIME_ZOMBIE (advisory)
        db.session.add(SymbolHit(
            symbol_id="route:bp.live_but_cold", symbol_kind="route",
            display_name="live_but_cold", module="backend.api.live_api",
            mode_flags=2, hit_count=1, last_fired_at=old, static_reachability=None,
        ))
        # fresh fire, module NOT in static map -> CONTEXTUAL_DISCOVERY (info)
        db.session.add(SymbolHit(
            symbol_id="task:dynamic_only", symbol_kind="task",
            display_name="dynamic_only", module="backend.tasks.ghost_task",
            mode_flags=1, hit_count=5, last_fired_at=now, static_reachability=None,
        ))
        db.session.commit()

        # --- fake static map: live_api is active; ghost_task absent ----------
        fake_map = {
            "root": str(tmp_path),
            "findings": [
                # a NEW HIGH dispatchable finding (backup-artifact) -> should dispatch
                {"kind": "backup-artifact", "severity": "high",
                 "summary": "Backup artifact x.BACK", "paths": ["x.BACK"],
                 "id": "newhigh01"},
                # a NEW HIGH non-dispatchable finding -> must NOT dispatch
                {"kind": "import-cycle", "severity": "high",
                 "summary": "cycle a->b", "paths": ["a.py"], "id": "newcyc01"},
            ],
            "stats": {},
            "node_meta": {
                "backend.api.live_api": {"lifecycle": "auto-loaded", "importers": 0},
            },
            "tool_graph": {}, "reachability": {}, "dependency_graph": {},
        }
        monkeypatch.setattr(rat, "build_map", lambda root: json.loads(json.dumps(fake_map)))

        dispatched = []
        monkeypatch.setattr(
            rat, "dispatch_finding",
            lambda f, priority="medium": dispatched.append(f["id"]) or {"success": True},
        )

        import backend.config as cfg
        monkeypatch.setattr(cfg, "GUAARDVARK_ROOT", str(tmp_path), raising=False)

        # --- run --------------------------------------------------------------
        result = rat._reconcile_and_emit_impl()
        assert result["status"] == "ok"

        # static_reachability set from the map
        rows = {r.symbol_id: r for r in SymbolHit.query.all()}
        assert rows["route:bp.live_but_cold"].static_reachability is True
        assert rows["task:dynamic_only"].static_reachability is False

        # liveness findings written into the stored snapshot and on their own
        payload = json.loads(store.snapshot_path(tmp_path).read_text())
        kinds = {f["kind"] for f in payload["findings"]}
        assert "runtime-zombie" in kinds          # reachable + cold
        assert "contextual-discovery" in kinds    # fired + not in static map
        stored = json.loads(store.liveness_path(tmp_path).read_text())
        assert {f["kind"] for f in stored["findings"]} >= {"runtime-zombie", "contextual-discovery"}

        # ONLY the new HIGH dispatchable finding was dispatched.
        assert dispatched == ["newhigh01"]
        assert "newcyc01" not in dispatched
        liveness_kinds = {"runtime-zombie", "contextual-discovery", "hot-path-spike"}
        liveness_ids = {f["id"] for f in payload["findings"]
                        if f["kind"] in liveness_kinds}
        assert liveness_ids.isdisjoint(set(dispatched))


def test_a_later_rebuild_keeps_the_liveness_findings(monkeypatch, tmp_path):
    """A static rebuild between two audits merges the stored liveness back in
    instead of dropping it."""
    from backend.services.system_mapper import store

    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path / "storage"))
    store.write_liveness(tmp_path, [{"id": "z1", "kind": "runtime-zombie", "severity": "high",
                                     "summary": "cold", "paths": []}], {"runtime_zombies": 1})
    payload = {"findings": [{"id": "s1", "kind": "dormant-module"},
                            {"id": "old", "kind": "runtime-zombie"}], "stats": {}}
    merged = store.merge_liveness(tmp_path, payload)
    assert [f["id"] for f in merged["findings"]] == ["s1", "z1"]
    assert merged["stats"]["liveness"]["runtime_zombies"] == 1


def test_reconcile_does_not_redispatch_prior_findings(app, monkeypatch, tmp_path):
    """A HIGH dispatchable finding already in the PRIOR snapshot is not re-dispatched."""
    import json
    from backend.tasks import runtime_audit_tasks as rat
    from backend.services.system_mapper import store
    import backend.config as cfg

    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path / "storage"))
    with app.app_context():
        store.write_json_atomic(store.snapshot_path(tmp_path), {
            "findings": [{"kind": "backup-artifact", "severity": "high",
                          "summary": "Backup artifact x.BACK", "paths": ["x.BACK"],
                          "id": "newhigh01"}],
        })

        fake_map = {
            "findings": [{"kind": "backup-artifact", "severity": "high",
                          "summary": "Backup artifact x.BACK", "paths": ["x.BACK"],
                          "id": "newhigh01"}],
            "stats": {}, "node_meta": {}, "tool_graph": {},
            "reachability": {}, "dependency_graph": {},
        }
        monkeypatch.setattr(rat, "build_map", lambda root: json.loads(json.dumps(fake_map)))
        monkeypatch.setattr(cfg, "GUAARDVARK_ROOT", str(tmp_path), raising=False)

        dispatched = []
        monkeypatch.setattr(
            rat, "dispatch_finding",
            lambda f, priority="medium": dispatched.append(f["id"]) or {"success": True},
        )

        result = rat._reconcile_and_emit_impl()
        assert result["status"] == "ok"
        assert dispatched == []  # already-seen finding is not re-dispatched


# ============================================================================
# B5: Flask middleware records a route hit without breaking the request.
# ============================================================================

def test_route_liveness_middleware_records_hit(monkeypatch):
    """Unit-test the after_request behaviour directly: a real route hit records a
    symbol hit; /api/health and 404s are skipped; an audit failure never breaks
    the request."""
    from backend.services.execution_context_tracker import (
        ExecutionContextTracker, get_tracker, MODE_FLASK_ROUTE,
    )
    import backend.services.execution_context_tracker as ectmod

    # Fresh tracker so we can inspect the buffer.
    tracker = ExecutionContextTracker()
    monkeypatch.setattr(ectmod, "_tracker", tracker)

    app = Flask(__name__)

    # Re-create the middleware behaviour exactly as app.py wires it.
    @app.after_request
    def record_route_liveness(response):
        try:
            from flask import request
            path = request.path or ""
            if (path.startswith('/socket.io')
                    or path.startswith('/api/health')
                    or request.endpoint is None
                    or response.status_code == 404):
                return response
            blueprint = request.blueprint or ""
            endpoint = request.endpoint or ""
            symbol_id = f"route:{blueprint}.{endpoint}"
            module = ""
            try:
                view = app.view_functions.get(endpoint)
                module = getattr(view, "__module__", "") or ""
            except Exception:
                module = ""
            get_tracker().record_hit(symbol_id, "route", endpoint, module, MODE_FLASK_ROUTE)
        except Exception:
            pass
        return response

    @app.route("/api/widgets")
    def widgets():
        return "ok"

    @app.route("/api/health/ping")
    def health_ping():
        return "ok"

    client = app.test_client()

    # A real route is recorded.
    resp = client.get("/api/widgets")
    assert resp.status_code == 200
    assert "route:.widgets" in tracker._buffer
    entry = tracker._buffer["route:.widgets"]
    assert entry["mode_flags"] == MODE_FLASK_ROUTE
    assert entry["kind"] == "route"

    # /api/health is skipped.
    client.get("/api/health/ping")
    assert "route:.health_ping" not in tracker._buffer

    # A 404 is skipped and the request still completes normally.
    resp404 = client.get("/nonexistent-path-xyz")
    assert resp404.status_code == 404
    assert not any(k.endswith("nonexistent") for k in tracker._buffer)


def test_route_liveness_middleware_never_breaks_request(monkeypatch):
    """If recording blows up, the request still returns its response."""
    import backend.services.execution_context_tracker as ectmod

    def _boom_tracker():
        raise RuntimeError("tracker exploded")

    monkeypatch.setattr(ectmod, "get_tracker", _boom_tracker)

    app = Flask(__name__)

    @app.after_request
    def record_route_liveness(response):
        try:
            from flask import request
            if request.endpoint is None or response.status_code == 404:
                return response
            ectmod.get_tracker().record_hit("route:x", "route", "x", "m", 2)
        except Exception:
            pass
        return response

    @app.route("/api/thing")
    def thing():
        return "still-ok"

    resp = app.test_client().get("/api/thing")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "still-ok"
