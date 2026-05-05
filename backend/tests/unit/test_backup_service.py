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


# Regression: shutil.ignore_patterns uses fnmatch, so 'venv' alone is an EXACT
# match — it doesn't catch sibling venvs like audio_foundry/venv-music. We
# learned that the hard way when a 5.9 GB music venv leaked into the code
# release zip and inflated it from ~3 MB to 409 MB.
def test_global_ignore_blocks_sibling_venvs():
    import shutil
    ignore = shutil.ignore_patterns(*backup_service.GLOBAL_IGNORE_PATTERNS)
    candidates = ["venv", "venv-music", "venv-pip", ".venv", ".venv-test", "env", "env-foo"]
    blocked = ignore("plugins/audio_foundry", candidates)
    for name in candidates:
        assert name in blocked, f"{name!r} should be ignored but slipped through"


# ──────────────────────────────────────────────────────────────────────────
# .env sanitizer for code-release backups. Goal: drop the zip on a new
# machine, run ./start.sh, and have it bootstrap cleanly. The sanitizer
# strips per-machine values (Redis password, DATABASE_URL, SECRET_KEY) so
# start.sh / start_redis.sh / start_postgres.sh regenerate them, while
# preserving account-level credentials so plugins keep working.

_FAKE_ROOT = "/home/alice/G002"
_FAKE_HOME = "/home/alice"


def test_sanitize_strips_redis_password():
    """Redis URL passwords baked from machine A must not travel to machine B —
    start_redis.sh keys on the absence of a password to provision a fresh one."""
    env = (
        "REDIS_URL=redis://:abc123secret@localhost:6379/0\n"
        "CELERY_BROKER_URL=redis://:abc123secret@localhost:6379/0\n"
        "CELERY_RESULT_BACKEND=redis://:abc123secret@localhost:6379/0\n"
    )
    out = backup_service.sanitize_env_for_release(env, _FAKE_ROOT, _FAKE_HOME)
    assert "abc123secret" not in out
    for key in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        assert f"{key}=redis://localhost:6379/0" in out


def test_sanitize_comments_out_secret_key():
    """SECRET_KEY must be regenerated per machine — start.sh detects an empty
    or missing line and writes a fresh one."""
    env = "SECRET_KEY=abcdef0123456789\n"
    out = backup_service.sanitize_env_for_release(env, _FAKE_ROOT, _FAKE_HOME)
    assert "abcdef0123456789" not in out
    assert "# SECRET_KEY=" in out


def test_sanitize_preserves_account_credentials():
    """Account-level keys ride along — the user wants Discord/Anthropic/HF
    to work on the new machine without manual re-entry."""
    env = (
        "ANTHROPIC_API_KEY=sk-ant-real-key\n"
        "DISCORD_BOT_TOKEN=discord-real-token\n"
        "HF_TOKEN=hf_real_token\n"
    )
    out = backup_service.sanitize_env_for_release(env, _FAKE_ROOT, _FAKE_HOME)
    assert "ANTHROPIC_API_KEY=sk-ant-real-key" in out
    assert "DISCORD_BOT_TOKEN=discord-real-token" in out
    assert "HF_TOKEN=hf_real_token" in out


def test_sanitize_strips_machine_paths():
    env = f"GUAARDVARK_ALLOWED_PATHS={_FAKE_ROOT}/data:{_FAKE_HOME}/Documents\n"
    out = backup_service.sanitize_env_for_release(env, _FAKE_ROOT, _FAKE_HOME)
    assert _FAKE_ROOT not in out
    assert _FAKE_HOME not in out
    assert "# GUAARDVARK_ALLOWED_PATHS=" in out


def test_sanitize_comments_database_url():
    env = "DATABASE_URL=postgresql://user:pw@localhost:5432/guaardvark\n"
    out = backup_service.sanitize_env_for_release(env, _FAKE_ROOT, _FAKE_HOME)
    assert "postgresql://" not in out
    assert "# DATABASE_URL=" in out


def test_sanitize_writes_warning_header():
    out = backup_service.sanitize_env_for_release("FOO=bar\n", _FAKE_ROOT, _FAKE_HOME)
    assert backup_service._SANITIZE_HEADER_MARKER in out
    assert "WARNING" in out
    assert "DISCORD_BOT_TOKEN" in out  # named in the warning


def test_sanitize_is_idempotent():
    """Re-sanitizing a sanitized .env (e.g. when machine B makes a release of
    its own install) must not stack duplicate headers."""
    env = "ANTHROPIC_API_KEY=sk-ant-real-key\n"
    once = backup_service.sanitize_env_for_release(env, _FAKE_ROOT, _FAKE_HOME)
    twice = backup_service.sanitize_env_for_release(once, _FAKE_ROOT, _FAKE_HOME)
    # Header should appear exactly once even after two passes
    assert twice.count(backup_service._SANITIZE_HEADER_MARKER) == 1
    # And the credential survives both rounds
    assert "ANTHROPIC_API_KEY=sk-ant-real-key" in twice


def test_sanitize_preserves_unrelated_lines():
    env = (
        "FLASK_PORT=5002\n"
        "VITE_PORT=5175\n"
        "GUAARDVARK_BROWSER_HEADLESS=true\n"
    )
    out = backup_service.sanitize_env_for_release(env, _FAKE_ROOT, _FAKE_HOME)
    assert "FLASK_PORT=5002" in out
    assert "VITE_PORT=5175" in out
    assert "GUAARDVARK_BROWSER_HEADLESS=true" in out
