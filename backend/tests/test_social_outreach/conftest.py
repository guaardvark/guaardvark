"""Test fixtures for social outreach suite."""
import pytest
from flask import Flask
from backend.models import db


@pytest.fixture
def app():
    """Flask app with in-memory database."""
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def db_session(app):
    """Database session that rolls back after test."""
    with app.app_context():
        yield db.session
