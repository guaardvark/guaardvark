"""An extension loads through core's hook points without core naming it,
a broken one is reported by id while the others still load, and a declared
url prefix with no route is an error rather than a silent 404."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend import extensions as X
from backend.models import db
from backend.services import task_handler_registry as R
from backend.utils.blueprint_discovery import BlueprintDiscovery

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "extensions" / "_template"


def _install(root: Path, ext_id: str) -> Path:
    dst = root / "extensions" / ext_id
    shutil.copytree(TEMPLATE, dst, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    # The template's blueprint name must stay unique per copy within one process.
    api = dst / "api" / "template_api.py"
    api.write_text(api.read_text().replace('"template_extension"', f'"{ext_id}_extension"'))
    return dst


def _forget_template_models():
    """Each test installs a fresh copy of the template; the previous copy's
    mapped class must leave the declarative registry or SQLAlchemy refuses the
    second definition of the same table."""
    for cls in list(db.Model.registry._class_registry.values()):
        if getattr(cls, "__tablename__", None) == "template_notes":
            db.Model.registry._dispose_cls(cls)
    table = db.metadata.tables.get("template_notes")
    if table is not None:
        db.metadata.remove(table)


@pytest.fixture
def root(tmp_path):
    for mod in [m for m in sys.modules if m.startswith("extensions.")]:
        sys.modules.pop(mod, None)
    _forget_template_models()
    X.reset_report()
    R.clear_task_handlers()
    (tmp_path / "extensions").mkdir()
    return tmp_path


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def test_discovery_skips_templates_and_honours_the_allow_list(root):
    _install(root, "acme")
    _install(root, "beta")
    (root / "extensions" / "_scratch").mkdir()
    (root / "extensions" / "_scratch" / "extension.json").write_text("{}")
    (root / "extensions" / "noext").mkdir()  # no manifest

    ids = [e.id for e in X.discover(root, environ={})]
    assert ids == ["acme", "beta"]
    assert [e.id for e in X.discover(root, environ={X.EXTENSIONS_ENV: "beta"})] == ["beta"]
    assert X.discover(root, environ={X.EXTENSIONS_ENV: "nothere"}) == []


def test_folder_name_is_the_id_and_bad_names_are_skipped(root):
    d = _install(root, "acme")
    (d / "extension.json").write_text(json.dumps({"id": "something-else", "name": "Acme"}))
    bad = root / "extensions" / "Bad-Name"
    bad.mkdir()
    (bad / "extension.json").write_text("{}")
    exts = X.discover(root, environ={})
    assert [e.id for e in exts] == ["acme"] and exts[0].name == "Acme"


def test_models_blueprints_migrations_and_seed_run_through_the_hooks(root, app, tmp_path):
    _install(root, "acme")
    exts = X.discover(root, environ={})

    assert X.import_models(exts) == {"acme": None}
    db.create_all()
    from sqlalchemy import inspect
    assert inspect(db.engine).has_table("template_notes")

    disc = BlueprintDiscovery(app)
    disc.auto_discover_and_register(app, directories=[], extension_directories=X.blueprint_directories(exts))
    assert app.test_client().get("/api/template/ping").status_code == 200
    assert X.missing_url_prefixes(app, exts[0]) == []

    assert X.run_migrations(exts, db) == {"acme": ["template_notes.pinned"]}
    assert "pinned" in {c["name"] for c in inspect(db.engine).get_columns("template_notes")}
    assert X.run_migrations(exts, db) == {"acme": []}  # idempotent

    stamps = tmp_path / "stamps"
    assert X.run_seeds(exts, app, stamp_dir=stamps) == {"acme": "seeded"}
    assert (stamps / "acme.seeded").read_text() == "0.1.0"
    assert X.run_seeds(exts, app, stamp_dir=stamps) == {"acme": "skipped"}


def test_a_broken_extension_is_reported_and_the_rest_still_load(root, app):
    _install(root, "acme")
    broken = _install(root, "broken")
    (broken / "models.py").write_text("this is not python\n")
    (broken / "api" / "template_api.py").write_text("import nothing_like_this\n")
    (broken / "extension.json").write_text(json.dumps({"name": "Broken", "url_prefixes": ["/api/broken"]}))
    exts = X.discover(root, environ={})

    result = X.import_models(exts)
    assert result["acme"] is None and "broken" in result and result["broken"]

    disc = BlueprintDiscovery(app)
    disc.auto_discover_and_register(app, directories=[], extension_directories=X.blueprint_directories(exts))
    assert X.missing_url_prefixes(app, exts[0]) == []
    assert X.missing_url_prefixes(app, exts[1]) == ["/api/broken"]


def test_extension_api_modules_named_config_are_not_dropped(root, app):
    d = _install(root, "acme")
    (d / "api" / "template_api.py").unlink()
    (d / "api" / "acme_config_api.py").write_text(
        'from flask import Blueprint\n'
        'bp = Blueprint("acme_config", __name__, url_prefix="/api/template")\n'
        '@bp.route("/config")\ndef cfg():\n    return {"ok": True}\n'
    )
    exts = X.discover(root, environ={})
    BlueprintDiscovery(app).auto_discover_and_register(app, directories=[], extension_directories=X.blueprint_directories(exts))
    assert app.test_client().get("/api/template/config").status_code == 200


def test_tasks_register_and_beat_merges(root):
    from celery import Celery
    _install(root, "acme")
    exts = X.discover(root, environ={})
    celery_app = Celery("test_ext")
    celery_app.conf.beat_schedule = {"core-thing": {"task": "core.thing", "schedule": 60.0}}
    celery_app.set_current()
    result = X.register_tasks(exts, celery_app)
    assert result == {"acme": ["extensions.acme.tasks.template_tasks"]}
    assert "template.heartbeat" in celery_app.tasks
    assert celery_app.conf.beat_schedule["template-heartbeat"] == {"task": "template.heartbeat", "schedule": 3600.0}
    assert "core-thing" in celery_app.conf.beat_schedule


def test_beat_entries_are_validated(root):
    d = _install(root, "acme")
    (d / "extension.json").write_text(json.dumps({"beat": {
        "ok": {"task": "t", "schedule": "30"},
        "no_task": {"schedule": 10},
        "bad_schedule": {"task": "t", "schedule": "soon"},
    }}))
    ext = X.discover(root, environ={})[0]
    assert ext.beat == {"ok": {"task": "t", "schedule": 30.0}}


def test_plugin_dirs_and_manifest_accessors(root):
    d = _install(root, "acme")
    (d / "plugin").mkdir()
    (d / "plugin" / "plugin.json").write_text('{"id": "acme_sidecar"}')
    ext = X.discover(root, environ={})[0]
    assert X.plugin_dirs([ext]) == [d / "plugin"]
    assert ext.url_prefixes == ["/api/template"]
    assert ext.system_deps == {"apt": []}
    assert ext.requirements_file == d / "requirements.txt"


def test_task_handler_registry():
    calls = []
    R.register_task_handler("acme_import", lambda task, progress: calls.append(task) or "done")
    R.register_task_handler("acme_import", lambda task, progress: "second")  # first wins
    assert R.get_task_handler("acme_import")({"id": 1}, lambda p, m: None) == "done"
    assert R.get_task_handler("other") is None and R.get_task_handler(None) is None
    assert R.registered_task_types() == ["acme_import"]
    with pytest.raises(ValueError):
        R.register_task_handler("", None)


def test_load_report_tracks_stages(root):
    X.record("acme", "models", True)
    X.record("acme", "blueprints", False, ["/api/x"])
    report = X.load_report()
    assert report["acme"]["ok"] is False
    assert report["acme"]["stages"]["blueprints"] == {"ok": False, "detail": ["/api/x"]}
