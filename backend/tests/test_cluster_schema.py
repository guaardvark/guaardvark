import json
import pytest

try:
    from flask import Flask
    from backend.models import db, InterconnectorNode
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


def test_interconnector_node_has_hardware_profile_and_online(app):
    with app.app_context():
        node = InterconnectorNode(
            node_id="test-node-1",
            node_name="test-node",
            node_mode="client",
            host="localhost",
            port=5002,
            hardware_profile=json.dumps({"arch": "x86_64"}),
        )
        db.session.add(node)
        db.session.commit()
        fetched = InterconnectorNode.query.filter_by(node_id="test-node-1").first()
        assert fetched.hardware_profile == json.dumps({"arch": "x86_64"})
        assert fetched.online is True
        assert not hasattr(fetched, "capabilities")
        d = fetched.to_dict()
        assert d["hardware_profile"] == {"arch": "x86_64"}
        assert "capabilities" not in d


def test_no_capabilities_references_in_backend():
    """Regression guard — no production backend code may reference
    InterconnectorNode.capabilities (removed in Task 1)."""
    import subprocess
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["grep", "-rn", r"\.capabilities", "backend/",
         "--include=*.py",
         "--exclude-dir=venv", "--exclude-dir=__pycache__",
         "--exclude-dir=tests", "--exclude-dir=mcp"],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    offending = [line for line in result.stdout.splitlines()
                 if ".capabilities" in line]
    assert not offending, (
        "Lingering .capabilities references in production backend code:\n"
        + "\n".join(offending)
    )
