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


# backend/services/connections/ owns an unrelated `capabilities`: the provider
# spec describing what a Connection may publish (registry.spec_for(...).capabilities).
# It has nothing to do with the InterconnectorNode column this guard protects, so
# it is skipped by path. Anything else that grows a `.capabilities` has to be added
# here deliberately, with a reason.
UNRELATED_CAPABILITIES_OWNERS = ("backend/services/connections/",)

SKIPPED_DIRS = {"venv", "__pycache__", "tests", "mcp", "node_modules", ".git"}


def test_no_capabilities_references_in_backend():
    """Regression guard — no production backend code may reference
    InterconnectorNode.capabilities (removed in Task 1).

    Matches `.capabilities` only on a word boundary, so `.capabilities_cache`
    (the Connection model's own column) is not a hit. Walks the tree in Python
    rather than shelling out to grep: the pattern needs \b, and `grep -P` is
    not available on macOS, which the project supports.
    """
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    backend = repo_root / "backend"
    pattern = re.compile(r"\.capabilities\b")

    offending = []
    for path in backend.rglob("*.py"):
        rel = path.relative_to(repo_root).as_posix()
        if SKIPPED_DIRS & set(path.relative_to(backend).parts):
            continue
        if rel.startswith(UNRELATED_CAPABILITIES_OWNERS):
            continue
        for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if pattern.search(line):
                offending.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offending, (
        "Lingering .capabilities references in production backend code:\n"
        + "\n".join(offending)
    )
