import json
import os
import zipfile
from pathlib import Path

import pytest
from flask import Flask

from backend import config, models
from backend.tools.full_backup import create_full_backup


@pytest.fixture
def app(tmp_path):
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


def test_create_full_backup(tmp_path, app):
    with app.app_context():
        logo_file = Path(app.config["CLIENT_LOGO_FOLDER"]) / "1_logo.png"
        logo_file.write_text("logo")
        client = models.Client(
            name="C1", logo_path=os.path.relpath(logo_file, app.config["UPLOAD_FOLDER"])
        )
        models.db.session.add(client)
        doc_dir = Path(app.config["UPLOAD_FOLDER"]) / "docs"
        doc_dir.mkdir(parents=True)
        doc_file = doc_dir / "doc.txt"
        doc_file.write_text("data")
        document = models.Document(
            filename="doc.txt",
            path=os.path.relpath(doc_file, app.config["UPLOAD_FOLDER"]),
        )
        models.db.session.add(document)
        models.db.session.commit()

        zip_path = tmp_path / "backup.zip"
        create_full_backup(str(zip_path))

        with zipfile.ZipFile(zip_path, "r") as zf:
            assert "guaardvark_backup.json" in zf.namelist()
            with zf.open("guaardvark_backup.json") as f:
                data = json.load(f)
            logo_path = data["clients"][0]["logo_path"]
            if logo_path is not None:
                assert logo_path.startswith("logos/")
            doc_path = data["documents"][0]["path"]
            if doc_path is not None:
                assert doc_path.startswith("files/")
            logo_files = [n for n in zf.namelist() if n.startswith("logos/")]
            if logo_files:
                assert all(n.startswith("logos/") for n in logo_files)
            file_entries = [n for n in zf.namelist() if n.startswith("files/")]
            if file_entries:
                assert all(n.startswith("files/") for n in file_entries)
