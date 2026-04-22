from unittest.mock import MagicMock
from datetime import datetime

import pytest

from backend.services.cluster_proxy import (
    WorkloadClassifier, LoopDetector, ProxyTargetResolver, NodeTarget,
    CLASSIFIER_RULES, ALWAYS_LOCAL_PREFIXES,
)
from backend.services.cluster_routing import RoutingTable, WorkloadRoute


# ---- app fixture ----------------------------------------------------

@pytest.fixture
def app():
    from flask import Flask
    from backend.models import db
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


# ---- classifier ----------------------------------------------------

def test_classify_chat():
    c = WorkloadClassifier()
    assert c.classify("POST", "/api/chat/unified") == "llm_chat"
    assert c.classify("POST", "/api/enhanced-chat") == "llm_chat"


def test_classify_batch_video_prefix():
    c = WorkloadClassifier()
    assert c.classify("POST", "/api/batch-video/generate/text") == "video_generation"
    assert c.classify("POST", "/api/batch-video/generate/image") == "video_generation"


def test_classify_batch_image_prefix():
    c = WorkloadClassifier()
    assert c.classify("POST", "/api/batch-image/generate/foo") == "image_generation"


def test_classify_indexing_embeddings():
    c = WorkloadClassifier()
    assert c.classify("POST", "/api/index/42") == "embeddings"
    assert c.classify("POST", "/api/index/bulk") == "embeddings"
    assert c.classify("POST", "/api/entity-indexing/index-all") == "embeddings"


def test_classify_search_semantic_only():
    c = WorkloadClassifier()
    assert c.classify("POST", "/api/search/semantic") == "rag_search"
    assert c.classify("POST", "/api/search/by-tag/foo") is None
    assert c.classify("POST", "/api/search/by-project/1") is None


def test_classify_voice_exact_paths():
    c = WorkloadClassifier()
    assert c.classify("POST", "/api/voice/speech-to-text") == "voice_stt"
    assert c.classify("POST", "/api/voice/text-to-speech") == "voice_tts"
    assert c.classify("POST", "/api/voice/test") is None


def test_classify_always_local_returns_none():
    c = WorkloadClassifier()
    for path in ("/api/health", "/api/settings/general", "/api/projects/1",
                 "/api/interconnector/nodes", "/api/cluster/fleet",
                 "/api/metadata-indexing/reindex", "/api/node/hardware-profile",
                 "/socket.io/polling"):
        assert c.classify("POST", path) is None, f"{path} should stay local"


def test_classify_get_on_workload_path_stays_local():
    c = WorkloadClassifier()
    assert c.classify("GET", "/api/chat/unified") is None


def test_classify_unknown_path_stays_local():
    c = WorkloadClassifier()
    assert c.classify("POST", "/api/something/random") is None


# ---- loop detector --------------------------------------------------

def test_loop_detector_force_local_at_max_hops():
    req = MagicMock()
    req.headers = {"X-Guaardvark-Hops": "2"}
    assert LoopDetector().should_force_local(req) is True
    req.headers = {"X-Guaardvark-Hops": "1"}
    assert LoopDetector().should_force_local(req) is False
    req.headers = {}
    assert LoopDetector().should_force_local(req) is False


def test_loop_detector_malformed_header_treated_as_zero():
    req = MagicMock()
    req.headers = {"X-Guaardvark-Hops": "not-a-number"}
    assert LoopDetector().should_force_local(req) is False


# ---- resolver -------------------------------------------------------

def _table_with(primary, fallback):
    r = WorkloadRoute(workload="llm_chat", mode="singular",
                      primary=primary, fallback=fallback, workers=[],
                      required_services=["ollama"], min_vram_mb=4096,
                      cpu_acceptable=False)
    return RoutingTable(routes={"llm_chat": r}, computed_at=datetime.utcnow(),
                        computed_by="master", node_count=3, fleet_hash="x")


def test_resolver_yields_primary_then_fallback_then_none(app):
    from backend.models import db, InterconnectorNode
    with app.app_context():
        for nid in ("res-a", "res-b", "res-c"):
            db.session.add(InterconnectorNode(
                node_id=nid, node_name=nid, node_mode="client",
                host=f"h-{nid}", port=5002, online=True))
        db.session.commit()

    t = _table_with("res-b", ["res-a", "res-c"])
    r = ProxyTargetResolver()
    with app.app_context():
        targets = list(r.resolve("llm_chat", t, local_node_id="res-a"))
    ids = [t.node_id for t in targets if t is not None]
    # res-b primary, then res-c (res-a is local, skipped), then None
    assert ids[0] == "res-b"
    assert "res-c" in ids
    assert "res-a" not in ids
    assert targets[-1] is None


def test_resolver_skips_offline_nodes(app):
    from backend.models import db, InterconnectorNode
    with app.app_context():
        db.session.add(InterconnectorNode(
            node_id="off-a", node_name="off-a", node_mode="client",
            host="h", port=5002, online=True))
        db.session.add(InterconnectorNode(
            node_id="off-b", node_name="off-b", node_mode="client",
            host="h", port=5002, online=False))
        db.session.commit()

    t = _table_with("off-b", ["off-a"])
    with app.app_context():
        targets = list(ProxyTargetResolver().resolve(
            "llm_chat", t, local_node_id="master"))
    ids = [t.node_id for t in targets if t is not None]
    assert "off-b" not in ids  # offline skipped
    assert "off-a" in ids


def test_resolver_yields_none_for_local_mode():
    from backend.services.cluster_routing import WorkloadRoute
    r = WorkloadRoute(workload="llm_chat", mode="local", primary=None,
                      fallback=[], workers=[], required_services=["ollama"],
                      min_vram_mb=4096, cpu_acceptable=False)
    t = RoutingTable(routes={"llm_chat": r}, computed_at=datetime.utcnow(),
                     computed_by="x", node_count=1, fleet_hash="x")
    targets = list(ProxyTargetResolver().resolve("llm_chat", t, local_node_id="me"))
    assert targets == [None]


def test_resolver_yields_none_for_missing_workload():
    t = RoutingTable(routes={}, computed_at=datetime.utcnow(),
                     computed_by="x", node_count=0, fleet_hash="x")
    targets = list(ProxyTargetResolver().resolve("llm_chat", t, local_node_id="me"))
    assert targets == [None]
