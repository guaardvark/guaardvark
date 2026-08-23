"""Address suggestions must work with no key, no network and no sources.

Guaardvark itself registers no address sources; a distribution adds its own.
These tests pin both halves so a provider change cannot take the field down.
"""

from __future__ import annotations

import logging

import pytest

try:
    from flask import Flask
    from sqlalchemy import Column, Integer, String

    from backend.api import addresses_api
    from backend.api.addresses_api import addresses_bp, register_local_source
    from backend.models import db
    from backend.services import address_lookup
except Exception:  # pragma: no cover - environment guard
    pytest.skip("Backend modules not available", allow_module_level=True)


class Place(db.Model):
    """Stand-in for whatever address-bearing table a distribution registers."""

    __tablename__ = "test_places"
    id = Column(Integer, primary_key=True)
    address = Column(String(255))
    city = Column(String(120))
    state = Column(String(32))
    zip = Column(String(20))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(addresses_api, "_LOCAL_SOURCES", [])
    application = Flask(__name__)
    application.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        }
    )
    db.init_app(application)
    application.register_blueprint(addresses_bp)
    with application.app_context():
        db.create_all()
        db.session.add(
            Place(address="210 O'Connor Dr", city="Elkhorn", state="WI", zip="53121")
        )
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def no_provider(monkeypatch):
    monkeypatch.setattr(address_lookup, "suggest", lambda *a, **k: [])


@pytest.mark.usefixtures("no_provider")
class TestWithoutSources:
    def test_upstream_default_returns_nothing_not_an_error(self, client):
        """Guaardvark registers no sources; the endpoint must still answer."""
        resp = client.get("/api/addresses?q=Elkhorn")
        assert resp.status_code == 200
        assert resp.get_json()["items"] == []


@pytest.mark.usefixtures("no_provider")
class TestWithARegisteredSource:
    def test_a_registered_table_is_searched(self, client):
        register_local_source(Place, "place")
        items = client.get("/api/addresses?q=O'Connor").get_json()["items"]
        assert [i["address"] for i in items] == ["210 O'Connor Dr"]
        assert items[0]["source"] == "place"

    def test_matches_city_and_zip_too(self, client):
        register_local_source(Place, "place")
        assert client.get("/api/addresses?q=Elkhorn").get_json()["items"]
        assert client.get("/api/addresses?q=53121").get_json()["items"]

    def test_registering_twice_does_not_duplicate_results(self, client):
        register_local_source(Place, "place")
        register_local_source(Place, "place")
        items = client.get("/api/addresses?q=O'Connor").get_json()["items"]
        assert len(items) == 1

    def test_short_query_returns_nothing(self, client):
        register_local_source(Place, "place")
        assert client.get("/api/addresses?q=E").get_json()["items"] == []

    def test_no_match_is_empty_not_an_error(self, client):
        register_local_source(Place, "place")
        resp = client.get("/api/addresses?q=Nowheresville")
        assert resp.status_code == 200
        assert resp.get_json()["items"] == []


class TestProviderGuards:
    def test_inert_without_a_key(self, monkeypatch):
        monkeypatch.setattr(address_lookup, "api_key", lambda: None)
        monkeypatch.setattr(address_lookup, "web_access_enabled", lambda: True)
        assert address_lookup.suggest("210 O'Connor Dr") == []
        assert "key" in address_lookup.unavailable_reason().lower()

    def test_inert_when_web_access_is_off(self, monkeypatch):
        monkeypatch.setattr(address_lookup, "api_key", lambda: "abc123")
        monkeypatch.setattr(address_lookup, "web_access_enabled", lambda: False)
        assert address_lookup.suggest("210 O'Connor Dr") == []
        assert "web access" in address_lookup.unavailable_reason().lower()

    def test_a_provider_failure_never_raises(self, monkeypatch):
        monkeypatch.setattr(address_lookup, "api_key", lambda: "abc123")
        monkeypatch.setattr(address_lookup, "web_access_enabled", lambda: True)

        def boom(*args, **kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(address_lookup, "_geoapify", boom)
        assert address_lookup.suggest("210 O'Connor Dr") == []


class TestTheKeyIsNeverLogged:
    """The provider key must not reach the log, on any path.

    CodeQL flagged the original: it read the key through the shared settings
    getter, which logs the setting name and the exception when a read fails.
    The key now has its own reader that logs nothing at all.
    """

    def test_reading_the_key_logs_nothing(self, app, caplog):
        from backend.models import Setting, db

        db.session.add(Setting(key=address_lookup.API_KEY_SETTING, value="secret-abc"))
        db.session.commit()
        with caplog.at_level(logging.DEBUG):
            assert address_lookup.api_key() == "secret-abc"
        assert caplog.records == []

    def test_a_failed_read_logs_nothing_either(self, app, caplog, monkeypatch):
        """The failure path is where a naive implementation leaks."""

        from backend.models import db as models_db

        def boom(*args, **kwargs):
            raise RuntimeError("db exploded")

        monkeypatch.setattr(models_db.session, "get", boom)
        with caplog.at_level(logging.DEBUG):
            assert address_lookup.api_key() is None
        assert caplog.records == []

    def test_the_key_never_appears_in_any_log_record(self, app, caplog):
        from backend.models import Setting, db

        db.session.add(Setting(key=address_lookup.API_KEY_SETTING, value="secret-abc"))
        db.session.commit()
        with caplog.at_level(logging.DEBUG):
            address_lookup.api_key()
            address_lookup.unavailable_reason()
            address_lookup.provider_name()
        assert not any("secret-abc" in r.getMessage() for r in caplog.records)
