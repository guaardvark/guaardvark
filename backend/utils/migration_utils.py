"""Migration utilities — simplified for schema-first approach.

Schema is managed by models.py + db.create_all().
These utilities handle Alembic stamping and health checks only.
"""
import os
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(ROOT_DIR, "migrations")


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


def get_database_revision(migrations_dir: str = MIGRATIONS_DIR) -> str:
    cfg = _alembic_config(migrations_dir)
    database_url = cfg.get_main_option("sqlalchemy.url")
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            return context.get_current_revision()
    except Exception:
        return None
    finally:
        engine.dispose()


def stamp_to_head(migrations_dir: str = MIGRATIONS_DIR) -> dict:
    """Stamp the database to the current migration head."""
    cfg = _alembic_config(migrations_dir)
    try:
        command.stamp(cfg, "head")
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()
        return {"success": True, "revision": head, "message": f"Stamped to {head}"}
    except Exception as e:
        return {"success": False, "message": f"Failed to stamp: {e}"}


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
    db_rev = get_database_revision(migrations_dir)

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
    health["action_needed"] = None if health["status"] == "ok" else "stamp"

    return health


def auto_upgrade(migrations_dir: str = MIGRATIONS_DIR) -> dict:
    """Auto-upgrade = just stamp to head (schema is managed by db.create_all)."""
    return stamp_to_head(migrations_dir)
