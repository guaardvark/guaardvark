import json
import os
import zipfile
from pathlib import Path

import pytest
from flask import Flask

from backend import config, models
from backend.services import backup_service


@pytest.fixture(autouse=True)
def _no_postgres_tools(monkeypatch):
    """Tripwire: nothing in this file may run pg_dump/pg_restore/psql.

    2026-08-29: test_restore_backup did — against the live database — and
    pg_restore --clean dropped every table. The app under test is sqlite; if
    a code path reaches for Postgres anyway, this fails the test on the spot.
    Tests that need to observe the pg_restore --list output patch
    subprocess.run themselves after this fixture."""
    import subprocess as _sp
    real_run = _sp.run

    def guarded_run(cmd, *a, **kw):
        exe = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd)
        if str(exe) in {"pg_dump", "pg_restore", "psql"}:
            raise AssertionError(f"test reached PostgreSQL tooling: {cmd[:3]}")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(_sp, "run", guarded_run)


@pytest.fixture
def app(tmp_path, monkeypatch):
    # A throwaway install root: data backups walk GUAARDVARK_ROOT/data and
    # restores extract under it. Left at the real checkout, each test packed
    # the real data/ tree (800MB) and the restore test wrote over it.
    root = tmp_path / "root"
    for d in ("data/uploads", "data/logos", "data/database"):
        (root / d).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", str(root))
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
        # create_backup("full") is the data backup with every component.
        assert meta["version"] == "1.0"
        assert meta["backup_type"] == "data"
        assert set(meta["components"]) == set(backup_service._ALL_COMPONENTS)
        assert "clients" in meta and meta["clients"]
        assert "documents" in meta and meta["documents"]


def test_granular_backup(tmp_path, app):
    with app.app_context():
        _create_sample_data(app)
        path = backup_service.create_backup("granular", ["clients", "tasks"])
        with zipfile.ZipFile(path, "r") as zf:
            meta = json.load(zf.open("guaardvark_backup.json"))
        # A component subset is still a data backup; the selection is the components list.
        assert meta["backup_type"] == "data"
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
        # The client's logo travels with the backup (its archive path is
        # relative to the project root, not a fixed logos/ folder).
        assert any(n.endswith("1_logo.png") for n in names), names


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


def test_code_release_includes_cluster_middleware(app):
    """Regression: cluster proxy middleware must ship in code-release zips."""
    with app.app_context():
        path = backup_service.create_code_release()
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            meta = json.load(zf.open("guaardvark_backup.json"))
        assert "backend/middleware/cluster_proxy_middleware.py" in names
        assert "backend/middleware/__init__.py" in names
        assert meta["backup_type"] == "code_release"


def test_code_release_includes_platform_and_docker_essentials(app):
    """Regression: Ubuntu/Docker/MCP paths omitted by the old allowlist must ship."""
    with app.app_context():
        path = backup_service.create_code_release()
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            install = zf.read("INSTALL.md").decode("utf-8")

        required = [
            "VERSION",
            ".gitignore",
            "setup.sh",
            "start-docker.sh",
            "docker-compose.gpu.yml",
            "docker/Dockerfile.backend",
            "backend/mcp/__main__.py",
            "backend/mcp/server.py",
            "backend/requirements-cv.txt",
            "backend/static/calibrate.html",
            "scripts/platform/linux.sh",
        ]
        missing = [p for p in required if p not in names]
        assert not missing, f"code release missing required paths: {missing}"

        assert "Ubuntu 26.04" in install
        assert "start-docker.sh" in install
        assert "backend/venv" in install  # wrong-venv troubleshooting tip
        assert "curl -fsSL https://guaardvark.com/install.sh | bash" in install
        assert "install.sh" in names  # bootstrap installer ships in the zip


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


# ---- the 2026-08-29 guards -------------------------------------------------

def test_sqlite_app_never_dumps_or_restores_the_configured_postgres(tmp_path, app, monkeypatch):
    """The app under test runs on sqlite; config.DATABASE_URL still names a
    Postgres. Backup and restore must use the app's database, never the
    configured one — with the tripwire above, reaching pg_* fails loudly."""
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://u:p@localhost:5432/guaardvark")
    with app.app_context():
        assert backup_service._effective_db_url().startswith("sqlite")
        _create_sample_data(app)
        path = backup_service.create_backup("full")
        with zipfile.ZipFile(path, "r") as zf:
            meta = json.load(zf.open("guaardvark_backup.json"))
        assert not meta.get("pg_dump_included")
        models.db.session.query(models.Client).delete()
        models.db.session.commit()
        summary = backup_service.restore_backup(path)
        assert summary.get("clients") == 1
        assert "pg_restore" not in summary


def test_restore_refuses_a_dump_of_another_database(tmp_path, monkeypatch):
    """A dump taken from a different database (another product, another
    machine) must never be restored here with --clean in front of it."""
    monkeypatch.setattr(backup_service, "_effective_db_url",
                        lambda: "postgresql://u:p@localhost:5432/guaardvark")
    monkeypatch.setattr(backup_service, "_dump_dbname", lambda _p: "roofbrain")
    dump = tmp_path / "foreign.pgdump"
    dump.write_bytes(b"PGDMP")
    assert backup_service._restore_pg_dump(dump) is False  # tripwire proves pg_restore never ran


def test_restore_toc_leaves_extensions_alone(tmp_path, monkeypatch):
    import subprocess
    listing = (
        ";\n; Archive created at 2026-08-29 01:23:46 EDT\n;     dbname: guaardvark\n;\n"
        "2; 3079 37380 EXTENSION - vector \n"
        "4630; 0 0 COMMENT - EXTENSION vector \n"
        "215; 1259 37400 TABLE public clients guaardvark\n"
        "4400; 0 37400 TABLE DATA public clients guaardvark\n"
    )

    def fake_run(cmd, *a, **kw):
        assert cmd[:2] == ["pg_restore", "--list"]
        return subprocess.CompletedProcess(cmd, 0, stdout=listing, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dump = tmp_path / "d.pgdump"; dump.write_bytes(b"PGDMP")
    toc = tmp_path / "d.toc"
    assert backup_service._write_restore_toc(dump, toc) is True
    kept = toc.read_text()
    assert "EXTENSION" not in kept
    assert "TABLE public clients" in kept and "TABLE DATA public clients" in kept
    assert backup_service._dump_dbname(dump) == "guaardvark"


def test_data_backup_does_not_sweep_the_real_install(tmp_path, app):
    """A test backup must contain only the fixture's data. If this grows past a
    few MB the service is walking the real checkout again."""
    with app.app_context():
        _create_sample_data(app)
        path = backup_service.create_backup("full")
        size_mb = Path(path).stat().st_size / (1024 * 1024)
        assert size_mb < 5, f"test backup is {size_mb:.0f}MB — real data is being packed"
        with zipfile.ZipFile(path, "r") as zf:
            assert not any(n.startswith("data/agent/") for n in zf.namelist())
