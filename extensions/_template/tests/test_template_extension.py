"""The template's own tests run from the extension folder."""
from extensions._template.api.template_api import template_bp


def test_blueprint_prefix_matches_manifest():
    assert template_bp.url_prefix == "/api/template"
