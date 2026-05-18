import json
import pytest

try:
    from flask import Flask
    from backend.models import db, InterconnectorNode, Setting
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    import backend.api.interconnector_api as ic_api

    application = Flask(__name__)
    application.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(application)

    from backend.api.interconnector_api import interconnector_bp
    application.register_blueprint(interconnector_bp)

    with application.app_context():
        db.create_all()
        # Configure as master with no API-key requirement so test POSTs go through.
        config = {
            "is_enabled": True,
            "node_mode": "master",
            "require_api_key": False,
        }
        setting = Setting(key="interconnector_config", value=json.dumps(config))
        db.session.add(setting)
        db.session.commit()

        # Reset the module-level config cache so the fixture config is picked up.
        ic_api._config_cache["config"] = None
        ic_api._config_cache["expires"] = 0

        yield application

        db.session.remove()
        db.drop_all()

        # Leave cache cleared for the next test.
        ic_api._config_cache["config"] = None
        ic_api._config_cache["expires"] = 0


def test_register_node_accepts_hardware_profile(app):
    from backend.models import db, InterconnectorNode
    client = app.test_client()
    profile = {"node_id": "hw-node-7", "arch": "x86_64",
               "services": {"ollama": {"installed": True}},
               "gpu": {"vendor": "nvidia", "vram_mb": 16384}}
    r = client.post("/api/interconnector/nodes/register",
                    json={"node_id": "hw-node-7",
                          "node_name": "hw-node-7",
                          "node_mode": "client",
                          "host": "192.168.1.20",
                          "port": 5002,
                          "hardware_profile": profile})
    assert r.status_code in (200, 201), f"got {r.status_code}: {r.get_data(as_text=True)}"
    with app.app_context():
        node = InterconnectorNode.query.filter_by(node_id="hw-node-7").first()
        assert node is not None
        assert json.loads(node.hardware_profile) == profile


def test_register_node_falls_back_to_server_side_detection(app):
    """When payload lacks hardware_profile (older client), server runs
    HardwareDetector so the row still has structured data."""
    from backend.models import db, InterconnectorNode
    client = app.test_client()
    r = client.post("/api/interconnector/nodes/register",
                    json={"node_id": "legacy-node-7",
                          "node_name": "legacy-node-7",
                          "node_mode": "client",
                          "host": "192.168.1.30",
                          "port": 5002})
    assert r.status_code in (200, 201), f"got {r.status_code}: {r.get_data(as_text=True)}"
    with app.app_context():
        node = InterconnectorNode.query.filter_by(node_id="legacy-node-7").first()
        profile = json.loads(node.hardware_profile)
        assert profile.get("arch") in ("x86_64", "aarch64", "arm64")
        assert "services" in profile


def test_register_node_seeds_fleet_map(app):
    """Every successful registration updates the in-memory FleetMap
    so routing decisions see the new node immediately."""
    from backend.services.fleet_map import get_fleet_map
    client = app.test_client()
    profile = {"node_id": "seed-node", "arch": "x86_64",
               "services": {"ollama": {"installed": True}},
               "gpu": {"vendor": "nvidia", "vram_mb": 8192}}
    r = client.post("/api/interconnector/nodes/register",
                    json={"node_id": "seed-node",
                          "node_name": "seed-node",
                          "node_mode": "client",
                          "host": "h", "port": 5002,
                          "hardware_profile": profile})
    assert r.status_code in (200, 201)
    fm_profile = get_fleet_map().get_profile("seed-node")
    assert fm_profile is not None
    assert fm_profile["gpu"]["vram_mb"] == 8192


def test_broadcast_updates_profile_when_client_reports_change(app):
    """When a client's sync response includes hardware_profile_updated,
    master pulls fresh profile and updates DB + FleetMap."""
    from unittest.mock import patch, MagicMock
    from backend.models import db, InterconnectorNode, InterconnectorBroadcastTarget
    from backend.services.fleet_map import get_fleet_map

    with app.app_context():
        node = InterconnectorNode(node_id="push-node", node_name="push-node",
                                  node_mode="client", host="192.168.1.40",
                                  port=5002, hardware_profile='{"arch": "x86_64"}')
        db.session.add(node)
        # broadcast_push_to_client also queries InterconnectorBroadcastTarget
        target = InterconnectorBroadcastTarget(broadcast_id="bc-1", node_id="push-node",
                                               status="pending")
        db.session.add(target)
        db.session.commit()

    updated = {"arch": "x86_64", "gpu": {"vendor": "nvidia", "vram_mb": 24576},
               "services": {"ollama": {"installed": True}}}
    mock_post = MagicMock(status_code=200)
    mock_post.json.return_value = {"ok": True, "hardware_profile_updated": True}
    mock_get = MagicMock(status_code=200)
    mock_get.json.return_value = updated

    with app.app_context():
        with patch("requests.post", return_value=mock_post):
            with patch("requests.get", return_value=mock_get):
                from backend.api.interconnector_api import broadcast_push_to_client
                # Celery bind=True task — call .run() to bypass broker entirely
                broadcast_push_to_client.run("bc-1", "push-node", {"type": "test"})

    with app.app_context():
        node = InterconnectorNode.query.filter_by(node_id="push-node").first()
        import json as j
        profile = j.loads(node.hardware_profile)
        assert profile.get("gpu", {}).get("vram_mb") == 24576, (
            f"expected profile update in DB, got {profile}"
        )
    assert get_fleet_map().get_profile("push-node")["gpu"]["vram_mb"] == 24576
