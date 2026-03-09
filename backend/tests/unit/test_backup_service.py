import json
import os
import zipfile
from pathlib import Path

import pytest
from flask import Flask

from backend import config, models
from backend.services import backup_service


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        config, "CLIENT_LOGO_FOLDER", str(Path(tmp_path / "uploads") / "logos")
    )
    app = Flask(__name__)
    app.config.from_object(config)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    app.config["CLIENT_LOGO_FOLDER"] = str(Path(app.config["UPLOAD_FOLDER"]) / "logos")
    os.makedirs(app.config["CLIENT_LOGO_FOLDER"], exist_ok=True)
    models.db.init_app(app)
    with app.app_context():
        models.db.create_all()
        yield app
        models.db.session.remove()
        models.db.drop_all()


def _create_sample_data(app):
    logo_file = Path(app.config["CLIENT_LOGO_FOLDER"]) / "1_logo.png"
    logo_file.write_text("logo")
    client = models.Client(
        name="C1", logo_path=os.path.relpath(logo_file, app.config["UPLOAD_FOLDER"])
    )
    models.db.session.add(client)
    proj = models.Project(name="P1", client_id=1)
    models.db.session.add(proj)
    doc_dir = Path(app.config["UPLOAD_FOLDER"]) / "docs"
    doc_dir.mkdir(parents=True)
    doc_file = doc_dir / "doc.txt"
    doc_file.write_text("data")
    document = models.Document(
        filename="doc.txt", path=os.path.relpath(doc_file, app.config["UPLOAD_FOLDER"])
    )
    models.db.session.add(document)
    task = models.Task(name="T1")
    models.db.session.add(task)
    rule = models.Rule(name="R1", level="SYSTEM", rule_text="x")
    models.db.session.add(rule)
    models.db.session.commit()


def test_full_backup(tmp_path, app):
    with app.app_context():
        _create_sample_data(app)
        path = backup_service.create_backup("full")
        assert Path(path).is_file()
        with zipfile.ZipFile(path, "r") as zf:
            meta = json.load(zf.open("guaardvark_backup.json"))
        assert meta["version"] == "2.0"
        assert meta["backup_type"] == "full"
        assert set(meta["components"]) == set(
            ["clients", "documents", "projects", "tasks", "websites", "chats", "rules", "system_settings"]
        )
        assert "clients" in meta and meta["clients"]
        assert "documents" in meta and meta["documents"]


def test_granular_backup(tmp_path, app):
    with app.app_context():
        _create_sample_data(app)
        path = backup_service.create_backup("granular", ["clients", "tasks"])
        with zipfile.ZipFile(path, "r") as zf:
            meta = json.load(zf.open("guaardvark_backup.json"))
        assert meta["backup_type"] == "granular"
        assert set(meta["components"]) == {"clients", "tasks"}
        assert "clients" in meta
        assert "tasks" in meta
        assert "documents" not in meta


def test_restore_backup(tmp_path, app):
    with app.app_context():
        _create_sample_data(app)
        path = backup_service.create_backup("full")
        models.db.session.query(models.Client).delete()
        models.db.session.query(models.Project).delete()
        models.db.session.query(models.Document).delete()
        models.db.session.commit()
        summary = backup_service.restore_backup(path)
        assert summary.get("clients") == 1
        assert models.Client.query.count() == 1
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
        assert any(n.startswith("logos/") for n in names)


def test_missing_file_in_backup(tmp_path, app):
    with app.app_context():
        client = models.Client(name="C2", logo_path="logos/missing.png")
        models.db.session.add(client)
        models.db.session.commit()
        path = backup_service.create_backup("granular", ["clients"])
        with zipfile.ZipFile(path, "r") as zf:
            meta = json.load(zf.open("guaardvark_backup.json"))
        assert meta["clients"][0]["logo_path"].endswith("missing.png")
