import pytest


@pytest.fixture(autouse=True)
def _no_real_port_holders(monkeypatch):
    """`launch --full` asks which install holds the backend and web UI ports
    before it starts anything. Under test the answer is "nobody", so a test
    neither depends on what runs on this machine nor reads its listeners; a
    test of the refusal supplies its own holders."""
    try:
        from llx import port_owner
    except ImportError:
        return
    monkeypatch.setattr(port_owner, "other_install_holders", lambda root, ports: [])
