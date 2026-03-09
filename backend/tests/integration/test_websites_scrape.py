import pytest

try:
    from flask import Flask

    from backend.api.websites_api import websites_bp
    from backend.models import Website, db
    from backend.utils import web_scraper
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    if websites_bp.name not in app.blueprints:
        app.register_blueprint(websites_bp)
    with app.app_context():
        db.create_all()
        site = Website(url="https://example.com", project_id=None)
        db.session.add(site)
        db.session.commit()
        site_id = site.id

        def fake_scrape(url):
            return {
                "url": url,
                "slug": "index",
                "content": "hello",
                "keywords": "k",
                "title": "Example",
                "featured_image": "img.jpg",
                "metadata": {"description": "test"},
                "category": "cat",
                "sitemaps": ["https://example.com/sitemap.xml"],
            }

        monkeypatch.setattr(web_scraper, "scrape_website", fake_scrape)
        yield app.test_client(), site_id
        db.session.remove()
        db.drop_all()


def test_scrape_route(client):
    c, site_id = client
    resp = c.post(f"/api/websites/{site_id}/scrape")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["url"] == "https://example.com"
    assert "sitemap.xml" in data["sitemaps"][0]
