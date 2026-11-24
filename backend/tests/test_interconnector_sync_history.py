"""A client's sync history is read under the id it is written under.

Manual syncs and applied updates record history under this machine's id
(backend/utils/node_identity.py); /sync/history used to look it up by the node
name and so always came back empty. Rows recorded under the node name by
installs that predate the machine id still show.
"""
import json
from datetime import datetime, timedelta

import pytest
from flask import Flask

from backend.api import interconnector_api as ic_api
from backend.models import InterconnectorNode, InterconnectorSyncHistory, Setting, db
from backend.utils import node_identity


@pytest.fixture
def client_app(tmp_path, monkeypatch):
    monkeypatch.setattr(node_identity, "DEFAULT_NODE_ID_PATH", tmp_path / "node_id")
    monkeypatch.setenv(node_identity.NODE_ID_ENV, "machine-a")
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(ic_api.interconnector_bp)
    with app.app_context():
        db.create_all()
        db.session.add(Setting(key="interconnector_config", value=json.dumps(
            {"is_enabled": True, "node_mode": "client", "node_name": "node-b"})))
        for node_id in ("machine-a", "node-b", "other-node"):
            db.session.add(InterconnectorNode(node_id=node_id, node_name=node_id, node_mode="client",
                                              host="127.0.0.1", port=5000))
        now = datetime.now()
        for node_id, minutes_ago in (("machine-a", 1), ("machine-a", 5), ("node-b", 60), ("other-node", 0)):
            db.session.add(InterconnectorSyncHistory(
                node_id=node_id, sync_direction="pull", status="success",
                sync_timestamp=now - timedelta(minutes=minutes_ago)))
        db.session.commit()
        ic_api._config_cache.update(config=None, expires=0)
        yield app
        db.session.remove()
        db.drop_all()
        ic_api._config_cache.update(config=None, expires=0)


def test_history_written_under_the_machine_id_is_shown(client_app):
    body = client_app.test_client().get("/api/interconnector/sync/history").get_json()["data"]
    assert [row["node_id"] for row in body["history"]] == ["machine-a", "machine-a", "node-b"]
    assert body["latest_sync"]["node_id"] == "machine-a"
    assert body["total_records"] == 3


def test_another_nodes_history_is_not_shown(client_app):
    body = client_app.test_client().get("/api/interconnector/sync/history?limit=50").get_json()["data"]
    assert "other-node" not in {row["node_id"] for row in body["history"]}


def test_the_id_read_is_the_id_syncs_record_under(client_app):
    """trigger_manual_sync and apply_updates record under this_node_id()."""
    assert ic_api.this_node_id() == "machine-a"
