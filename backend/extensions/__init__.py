"""Client extensions: in-process code that lives outside core.

An extension is a folder under ``extensions/<id>/`` that core loads through
four hook points without any core file naming it:

    extension.json      metadata + the declarative bits (beat entries, url
                        prefixes, system deps)
    api/*.py            Flask blueprints — auto-discovered like backend/api
    models.py           SQLAlchemy models on the core ``db`` — imported before
                        create_all(); foreign keys to core tables by name
    tasks/*.py          Celery tasks — imported for side-effect registration
    migrations.py       ADD_COLUMNS = [(table, column, ddl)] + optional migrate(db)
    seed.py             seed(app) — idempotent; runs once per extension version
    profile.json        the distribution profile (see backend/profiles)
    plugin/plugin.json  an optional sidecar service, discovered like plugins/
    frontend/index.jsx  routes, nav, themes… (frontend/src/extensions.js)
    tests/              collected by pytest (testpaths includes extensions/)

This is a second plugin kind, not a reuse: ``backend/plugins`` runs sidecar
processes (port, health, start/stop) and has no notion of blueprints, models
or tasks. Extensions are the in-process half; an extension may ship a sidecar
in ``plugin/`` and the plugin registry picks it up.

Failures are loud but never fatal to boot: a broken extension is reported by
id in the log and in ``load_report()``, and every other extension still
loads. The lesson from the first vertical: a blueprint import error turned
into a warning 404s every route of the vertical with a clean-looking startup.

Presence is intent — every non-underscore folder with ``extension.json``
loads; ``GUAARDVARK_EXTENSIONS=a,b`` restricts to a list.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import re
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, MutableMapping, Optional

logger = logging.getLogger(__name__)

EXTENSIONS_ENV = "GUAARDVARK_EXTENSIONS"
MANIFEST = "extension.json"
PACKAGE = "extensions"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass
class Extension:
    id: str
    root: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.id)

    @property
    def version(self) -> str:
        return str(self.manifest.get("version") or "0.0.0")

    @property
    def package(self) -> str:
        return f"{PACKAGE}.{self.id}"

    @property
    def api_dir(self) -> Optional[Path]:
        d = self.root / "api"
        return d if d.is_dir() else None

    @property
    def plugin_dir(self) -> Optional[Path]:
        d = self.root / "plugin"
        return d if (d / "plugin.json").is_file() else None

    @property
    def frontend_dir(self) -> Optional[Path]:
        d = self.root / "frontend"
        return d if (d / "index.jsx").is_file() else None

    @property
    def beat(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key, entry in (self.manifest.get("beat") or {}).items():
            if not isinstance(entry, dict) or not entry.get("task"):
                logger.warning("extension %s: beat entry %r needs a task; ignored", self.id, key)
                continue
            try:
                schedule = float(entry.get("schedule"))
            except (TypeError, ValueError):
                logger.warning("extension %s: beat entry %r needs a numeric schedule (seconds); ignored", self.id, key)
                continue
            item = {"task": str(entry["task"]), "schedule": schedule}
            if isinstance(entry.get("options"), dict):
                item["options"] = dict(entry["options"])
            out[str(key)] = item
        return out

    @property
    def url_prefixes(self) -> list[str]:
        return [str(p) for p in (self.manifest.get("url_prefixes") or [])]

    @property
    def system_deps(self) -> dict[str, list[str]]:
        deps = self.manifest.get("system_deps") or {}
        return {str(k): [str(x) for x in v] for k, v in deps.items() if isinstance(v, list)}

    @property
    def requirements_file(self) -> Optional[Path]:
        p = self.root / "requirements.txt"
        return p if p.is_file() else None


# ─── discovery ────────────────────────────────────────────────────────────────

def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def extensions_root(root: Optional[Path] = None) -> Path:
    return (root or repo_root()) / PACKAGE


def enabled_ids(environ: Optional[MutableMapping[str, str]] = None) -> Optional[set[str]]:
    env = os.environ if environ is None else environ
    raw = (env.get(EXTENSIONS_ENV) or "").strip()
    if not raw:
        return None
    return {x.strip() for x in raw.split(",") if x.strip()}


def discover(root: Optional[Path] = None, environ: Optional[MutableMapping[str, str]] = None) -> list[Extension]:
    """Every loadable extension, sorted by id. Underscore folders are templates."""
    base = extensions_root(root)
    if not base.is_dir():
        return []
    allowed = enabled_ids(environ)
    found: list[Extension] = []
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        manifest_path = d / MANIFEST
        if not manifest_path.is_file():
            continue
        if not _ID_RE.match(d.name):
            logger.error("extension folder %r is not a valid id (lowercase, digits, underscore); skipped", d.name)
            continue
        if allowed is not None and d.name not in allowed:
            logger.info("extension %s present but not in %s; skipped", d.name, EXTENSIONS_ENV)
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("top level must be an object")
        except Exception as e:
            logger.error("extension %s: %s unreadable (%s); skipped", d.name, MANIFEST, e)
            continue
        declared = manifest.get("id")
        if declared and declared != d.name:
            logger.warning("extension %s: manifest id %r differs from the folder; the folder name wins", d.name, declared)
        found.append(Extension(id=d.name, root=d, manifest=manifest))
    if allowed is not None:
        for missing in sorted(allowed - {e.id for e in found}):
            logger.error("%s names %r but no such extension is installed", EXTENSIONS_ENV, missing)
    return found


# ─── importing ────────────────────────────────────────────────────────────────

def _bind_package(ext: Extension) -> None:
    """Make ``extensions.<id>`` importable from wherever the folder lives.

    The real ``extensions`` package sits at the repo root; a test may point the
    loader at a temporary root instead, so the subpackage is bound explicitly
    rather than resolved through sys.path.
    """
    parent = sys.modules.get(PACKAGE)
    if parent is None:
        parent = types.ModuleType(PACKAGE)
        parent.__path__ = [str(ext.root.parent)]  # type: ignore[attr-defined]
        sys.modules[PACKAGE] = parent
    existing = sys.modules.get(ext.package)
    if existing is not None and Path(getattr(existing, "__file__", "") or "").parent == ext.root:
        return
    init = ext.root / "__init__.py"
    if not init.is_file():
        init.write_text("", encoding="utf-8")
    spec = importlib.util.spec_from_file_location(ext.package, init, submodule_search_locations=[str(ext.root)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[ext.package] = module
    spec.loader.exec_module(module)
    setattr(parent, ext.id, module)


def import_models(exts: Iterable[Extension]) -> dict[str, Optional[str]]:
    """Import each extension's models before create_all(). Returns id -> error."""
    result: dict[str, Optional[str]] = {}
    for ext in exts:
        if not (ext.root / "models.py").is_file():
            continue
        try:
            _bind_package(ext)
            importlib.import_module(f"{ext.package}.models")
            result[ext.id] = None
        except Exception as e:
            logger.error("extension %s: models failed to import: %s", ext.id, e, exc_info=True)
            result[ext.id] = str(e)
    return result


def register_models(exts: Iterable[Extension]) -> dict[str, Optional[str]]:
    """Import each extension's optional ``media_models.py`` and call its
    ``register()``, which adds video model entries and families through
    ``video_model_registry.register_video_model`` / ``register_family_spec``.
    Runs after import_models. Returns id -> error."""
    result: dict[str, Optional[str]] = {}
    for ext in exts:
        if not (ext.root / "media_models.py").is_file():
            continue
        try:
            _bind_package(ext)
            module = importlib.import_module(f"{ext.package}.media_models")
            register = getattr(module, "register", None)
            if callable(register):
                register()
            result[ext.id] = None
        except Exception as e:
            logger.error("extension %s: media models failed to register: %s", ext.id, e, exc_info=True)
            result[ext.id] = str(e)
    return result


def blueprint_directories(exts: Iterable[Extension]) -> list[tuple[str, str]]:
    """(path, package) pairs for blueprint discovery."""
    dirs: list[tuple[str, str]] = []
    for ext in exts:
        if ext.api_dir is None:
            continue
        try:
            _bind_package(ext)
        except Exception as e:
            logger.error("extension %s: package failed to bind: %s", ext.id, e, exc_info=True)
            continue
        dirs.append((str(ext.api_dir), f"{ext.package}.api"))
    return dirs


def missing_url_prefixes(app, ext: Extension) -> list[str]:
    """Declared prefixes with no mounted rule — the silent-404 check."""
    rules = [r.rule for r in app.url_map.iter_rules()]
    return [p for p in ext.url_prefixes if not any(r.startswith(p) for r in rules)]


# ─── schema and seeds ─────────────────────────────────────────────────────────

def run_migrations(exts: Iterable[Extension], db, logger_=None) -> dict[str, list[str]]:
    """Apply each extension's ADD_COLUMNS (guarded by an inspector check, so
    plain ``ADD COLUMN`` DDL is enough and portable) then ``migrate(db)``.
    Returns id -> list of applied column names."""
    from sqlalchemy import inspect, text
    log = logger_ or logger
    applied: dict[str, list[str]] = {}
    for ext in exts:
        if not (ext.root / "migrations.py").is_file():
            continue
        try:
            _bind_package(ext)
            mod = importlib.import_module(f"{ext.package}.migrations")
        except Exception as e:
            log.error("extension %s: migrations failed to import: %s", ext.id, e, exc_info=True)
            continue
        done: list[str] = []
        for entry in getattr(mod, "ADD_COLUMNS", []) or []:
            try:
                table, column, ddl = entry
            except (TypeError, ValueError):
                log.warning("extension %s: ADD_COLUMNS entry %r is not (table, column, ddl); skipped", ext.id, entry)
                continue
            try:
                insp = inspect(db.engine)
                if not insp.has_table(table):
                    log.warning("extension %s: table %s does not exist; %s.%s not added", ext.id, table, table, column)
                    continue
                if any(c["name"] == column for c in insp.get_columns(table)):
                    continue
                db.session.execute(text(ddl))
                db.session.commit()
                done.append(f"{table}.{column}")
                log.info("extension %s: added %s.%s", ext.id, table, column)
            except Exception as e:
                log.warning("extension %s: could not add %s.%s: %s", ext.id, table, column, e)
                try:
                    db.session.rollback()
                except Exception:
                    pass
        migrate = getattr(mod, "migrate", None)
        if callable(migrate):
            try:
                migrate(db)
            except Exception as e:
                log.error("extension %s: migrate(db) failed: %s", ext.id, e, exc_info=True)
                try:
                    db.session.rollback()
                except Exception:
                    pass
        applied[ext.id] = done
    return applied


def run_seeds(exts: Iterable[Extension], app, stamp_dir: Optional[Path] = None) -> dict[str, str]:
    """Run ``seed(app)`` once per extension version; a stamp file remembers.
    Returns id -> "seeded" | "skipped" | "error: ...". Seeds must still be
    idempotent — the stamp is an optimisation, not the guarantee."""
    if stamp_dir is None:
        try:
            from backend.config import STORAGE_DIR
            stamp_dir = Path(STORAGE_DIR) / PACKAGE
        except Exception:
            stamp_dir = repo_root() / "data" / PACKAGE
    result: dict[str, str] = {}
    for ext in exts:
        if not (ext.root / "seed.py").is_file():
            continue
        stamp = stamp_dir / f"{ext.id}.seeded"
        try:
            if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == ext.version:
                result[ext.id] = "skipped"
                continue
            _bind_package(ext)
            mod = importlib.import_module(f"{ext.package}.seed")
            seed = getattr(mod, "seed", None)
            if not callable(seed):
                result[ext.id] = "error: seed.py has no seed(app)"
                continue
            seed(app)
            stamp_dir.mkdir(parents=True, exist_ok=True)
            stamp.write_text(ext.version, encoding="utf-8")
            result[ext.id] = "seeded"
        except Exception as e:
            logger.error("extension %s: seed failed: %s", ext.id, e, exc_info=True)
            result[ext.id] = f"error: {e}"
    return result


# ─── celery ───────────────────────────────────────────────────────────────────

def register_tasks(exts: Iterable[Extension], celery_app) -> dict[str, list[str]]:
    """Import ``tasks/*.py`` (tasks register themselves at import) and merge
    the manifest's beat entries. Returns id -> imported module names."""
    result: dict[str, list[str]] = {}
    for ext in exts:
        tasks_dir = ext.root / "tasks"
        modules: list[str] = []
        if tasks_dir.is_dir():
            try:
                _bind_package(ext)
            except Exception as e:
                logger.error("extension %s: package failed to bind: %s", ext.id, e, exc_info=True)
                continue
            for py in sorted(tasks_dir.glob("*.py")):
                if py.name.startswith(("_", ".")):
                    continue
                name = f"{ext.package}.tasks.{py.stem}"
                try:
                    importlib.import_module(name)
                    modules.append(name)
                except Exception as e:
                    logger.error("extension %s: task module %s failed: %s", ext.id, py.stem, e, exc_info=True)
        beat = ext.beat
        if beat:
            schedule = getattr(celery_app.conf, "beat_schedule", None) or {}
            schedule.update(beat)
            celery_app.conf.beat_schedule = schedule
        result[ext.id] = modules
    return result


# ─── plugins shipped by an extension ──────────────────────────────────────────

def plugin_dirs(exts: Iterable[Extension]) -> list[Path]:
    return [ext.plugin_dir for ext in exts if ext.plugin_dir is not None]


# ─── report ───────────────────────────────────────────────────────────────────

_report: dict[str, dict[str, Any]] = {}


def record(ext_id: str, stage: str, ok: bool, detail: Any = None) -> None:
    entry = _report.setdefault(ext_id, {"ok": True, "stages": {}})
    entry["stages"][stage] = {"ok": ok, "detail": detail}
    if not ok:
        entry["ok"] = False


def load_report() -> dict[str, dict[str, Any]]:
    """Per-extension outcome of every stage this process ran."""
    return {k: {"ok": v["ok"], "stages": dict(v["stages"])} for k, v in _report.items()}


def reset_report() -> None:
    _report.clear()
