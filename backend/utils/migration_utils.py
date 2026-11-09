"""Migration utilities — simplified for schema-first approach.

Schema is managed by models.py + db.create_all().
These utilities handle Alembic stamping, health checks, and pruning stale
revision files left behind by interconnector sync (additive-only file copy).
"""
import logging
import os
import shutil
from pathlib import Path
from typing import Iterable, Optional

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(ROOT_DIR, "migrations")

# Consolidated schema floor (2026-06-19). When multiple Alembic heads exist on a
# client, keep this chain and delete orphaned pre-squash revision files.
PREFERRED_BASELINE_REVISION = "v2_6_1_baseline"

VERSIONS_PREFIX = "backend/migrations/versions/"


def _alembic_config(migrations_dir: str = MIGRATIONS_DIR) -> Config:
    cfg_path = os.path.join(migrations_dir, "alembic.ini")
    cfg = Config(cfg_path)
    cfg.set_main_option("script_location", migrations_dir)
    try:
        from backend.config import DATABASE_URL as DEFAULT_DATABASE_URL
    except Exception:
        DEFAULT_DATABASE_URL = "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"
    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def get_heads(migrations_dir: str = MIGRATIONS_DIR):
    cfg = _alembic_config(migrations_dir)
    script = ScriptDirectory.from_config(cfg)
    return script.get_heads()


def _read_database_revision(migrations_dir: str = MIGRATIONS_DIR):
    """(revision, None), or (None, reason) when the database can't be reached.

    A database with no alembic_version row is reachable and returns (None, None).
    """
    cfg = _alembic_config(migrations_dir)
    database_url = cfg.get_main_option("sqlalchemy.url")
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            return context.get_current_revision(), None
    except Exception as exc:
        reason = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        return None, reason[:200]
    finally:
        engine.dispose()


def get_database_revision(migrations_dir: str = MIGRATIONS_DIR) -> str:
    return _read_database_revision(migrations_dir)[0]


def _revision_paths_for_head(migrations_dir: str, head: str) -> set[Path]:
    cfg = _alembic_config(migrations_dir)
    script = ScriptDirectory.from_config(cfg)
    keep: set[Path] = set()
    for rev in script.walk_revisions(base="base", head=head):
        if rev.path:
            keep.add(Path(rev.path).resolve())
    return keep


def _chain_reaches(migrations_dir: str, head: str, revision: str) -> bool:
    """Whether ``revision`` is on the chain from base to ``head``."""
    try:
        cfg = _alembic_config(migrations_dir)
        script = ScriptDirectory.from_config(cfg)
        return any(rev.revision == revision for rev in script.walk_revisions(base="base", head=head))
    except Exception:
        return False


def prune_stale_migration_versions(
    migrations_dir: str = MIGRATIONS_DIR,
    *,
    authoritative_paths: Optional[Iterable[str]] = None,
) -> dict:
    """Remove migration version files that are not part of the master schema chain.

    Interconnector sync overwrites changed files but never deletes files the master
    removed (e.g. after the v2.6.1 squash). Leftover revision modules create multiple
    Alembic heads and break ``stamp head``.

    When *authoritative_paths* is provided (paths under ``backend/migrations/versions/``
    from a master file scan), only those ``.py`` files are kept. Otherwise the kept
    chain is derived from the preferred baseline head, or the sole head when unambiguous.
    """
    versions_dir = Path(migrations_dir) / "versions"
    result = {
        "removed": [],
        "kept": [],
        "heads_before": [],
        "heads_after": [],
    }
    if not versions_dir.is_dir():
        return result

    try:
        result["heads_before"] = list(get_heads(migrations_dir))
    except Exception as exc:
        result["error"] = f"could not read heads before prune: {exc}"
        return result

    keep_paths: set[Path] = set()

    if authoritative_paths is not None:
        for rel in authoritative_paths:
            rel_norm = str(rel).replace("\\", "/")
            if not rel_norm.startswith(VERSIONS_PREFIX):
                continue
            name = Path(rel_norm).name
            if name in ("__init__.py",) or not name.endswith(".py"):
                continue
            keep_paths.add((versions_dir / name).resolve())
    else:
        heads = result["heads_before"]
        if not heads:
            return result
        on_baseline = [h for h in heads if _chain_reaches(migrations_dir, h, PREFERRED_BASELINE_REVISION)]
        if len(heads) == 1:
            preferred_head = heads[0]
        elif PREFERRED_BASELINE_REVISION in heads:
            preferred_head = PREFERRED_BASELINE_REVISION
        elif len(on_baseline) == 1:
            # A migration on top of the baseline is the live chain; the other heads
            # are pre-squash leftovers.
            preferred_head = on_baseline[0]
        else:
            preferred_head = sorted(heads)[-1]
            logger.warning(
                "Multiple migration heads %s — pruning to last-resort head %s",
                heads,
                preferred_head,
            )
        try:
            keep_paths = _revision_paths_for_head(migrations_dir, preferred_head)
        except Exception as exc:
            result["error"] = f"could not resolve revision chain for {preferred_head}: {exc}"
            return result

    if not keep_paths:
        return result

    for py_file in sorted(versions_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            result["kept"].append(py_file.name)
            continue
        if py_file.resolve() in keep_paths:
            result["kept"].append(py_file.name)
            continue
        try:
            py_file.unlink()
            result["removed"].append(py_file.name)
            logger.info("Removed stale migration version file: %s", py_file.name)
        except OSError as exc:
            logger.warning("Failed to remove stale migration %s: %s", py_file.name, exc)

    if result["removed"]:
        shutil.rmtree(versions_dir / "__pycache__", ignore_errors=True)
        try:
            result["heads_after"] = list(get_heads(migrations_dir))
        except Exception as exc:
            result["heads_after"] = []
            result["error"] = f"pruned files but could not re-read heads: {exc}"
    else:
        result["heads_after"] = result["heads_before"]

    return result


def stamp_to_head(migrations_dir: str = MIGRATIONS_DIR) -> dict:
    """Stamp the database to the current migration head."""
    prune_result = prune_stale_migration_versions(migrations_dir)
    cfg = _alembic_config(migrations_dir)
    try:
        command.stamp(cfg, "head")
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()
        msg = f"Stamped to {head}"
        if prune_result.get("removed"):
            msg += f" (pruned stale versions: {', '.join(prune_result['removed'])})"
        return {
            "success": True,
            "revision": head,
            "message": msg,
            "prune": prune_result,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to stamp: {e}",
            "prune": prune_result,
        }


def ensure_single_head(migrations_dir: str = MIGRATIONS_DIR, auto_merge: bool = False):
    """Check that there is exactly one migration head."""
    heads = get_heads(migrations_dir)
    if len(heads) > 1:
        raise RuntimeError(
            f"Multiple migration heads detected: {heads}. "
            f"This should not happen with the consolidated schema approach."
        )
    return heads[0] if heads else None


def get_health(migrations_dir: str = MIGRATIONS_DIR) -> dict:
    """Get migration health status."""
    cfg = _alembic_config(migrations_dir)
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    head = heads[0] if heads else None
    db_rev, db_error = _read_database_revision(migrations_dir)

    # Without a connection the revision is unknown, not behind: saying
    # "needs re-stamp" here sends people to the wrong fix.
    if db_error is not None:
        return {"status": "db_unreachable", "head": head, "db_revision": None, "error": db_error}

    if len(heads) > 1:
        return {"status": "multiple_heads", "heads": list(heads), "db_revision": db_rev}

    if db_rev != head:
        return {"status": "needs_stamp", "head": head, "db_revision": db_rev}

    return {"status": "ok", "head": head, "db_revision": db_rev}


def get_comprehensive_health(migrations_dir: str = MIGRATIONS_DIR) -> dict:
    """Comprehensive health check — used by check_migrations.py."""
    health = get_health(migrations_dir)

    # Map to the status codes check_migrations.py expects
    if health["status"] == "multiple_heads":
        health["heads"] = health.get("heads", [])
    elif health["status"] == "needs_stamp":
        health["pending_migrations"] = [health.get("head", "unknown")]
        health["has_pending"] = True
    else:
        health["pending_migrations"] = []
        health["has_pending"] = False

    # No model change detection needed — db.create_all() handles schema
    health["model_changes"] = {"has_changes": False, "summary": "N/A -- schema-first approach"}
    health["has_model_changes"] = False
    health["current"] = health.get("head")
    health["action_needed"] = {"ok": None, "db_unreachable": "start_database"}.get(health["status"], "stamp")

    return health


def auto_upgrade(migrations_dir: str = MIGRATIONS_DIR) -> dict:
    """Auto-upgrade = prune stale versions + stamp to head (schema via db.create_all)."""
    return stamp_to_head(migrations_dir)
