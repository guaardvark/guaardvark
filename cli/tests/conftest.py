import pytest


@pytest.fixture(autouse=True)
def _no_real_port_holders(request, monkeypatch):
    """The CLI asks which install holds the backend and web UI ports before it
    starts or trusts anything. Under test the answer is "nobody", so a test
    neither depends on what runs on this machine nor reads its listeners; a
    test of a refusal supplies its own holders. Tests marked
    `real_port_holders` get the real lookup, against listeners they start
    themselves."""
    if request.node.get_closest_marker("real_port_holders"):
        return
    try:
        from llx import port_owner
    except ImportError:
        return
    monkeypatch.setattr(port_owner, "other_install_holders", lambda root, ports: [])
