import pytest

pytestmark = pytest.mark.rules

try:
    from backend.app import app
except Exception:
    pytest.skip("Flask app not available", allow_module_level=True)


def test_all_routes_unique():
    with app.app_context():
        routes = [
            (
                rule.rule,
                tuple(sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})),
            )
            for rule in app.url_map.iter_rules()
        ]
    assert len(routes) == len(set(routes))
