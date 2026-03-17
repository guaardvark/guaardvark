import json
import os
import sys
import uuid
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(ROOT_DIR, "migrations")


def _alembic_config(migrations_dir: str = MIGRATIONS_DIR) -> Config:
    cfg_path = os.path.join(migrations_dir, "alembic.ini")
    cfg = Config(cfg_path)
    cfg.set_main_option("script_location", migrations_dir)
    try:
        from backend.config import DATABASE_URL as DEFAULT_DATABASE_URL
    except Exception:
        DEFAULT_DATABASE_URL = (
            "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"
        )
    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def get_heads(migrations_dir: str = MIGRATIONS_DIR):
    cfg = _alembic_config(migrations_dir)
    script = ScriptDirectory.from_config(cfg)
    return script.get_heads()


def merge_heads(
    heads, migrations_dir: str = MIGRATIONS_DIR, message: str = "merge heads"
):
    if len(heads) < 2:
        return None
    rev_id = uuid.uuid4().hex[:12]
    down_rev = "(" + ", ".join(repr(h) for h in heads) + ")"
    versions_dir = os.path.join(migrations_dir, "versions")
    os.makedirs(versions_dir, exist_ok=True)
    filename = f"{rev_id}_merge_heads.py"
    path = os.path.join(versions_dir, filename)
    with open(path, "w") as fh:
        fh.write(
            f"from alembic import op\nimport sqlalchemy as sa\n\n"
            f"revision = '{rev_id}'\n"
            f"down_revision = {down_rev}\n"
            "branch_labels = None\n"
            "depends_on = None\n\n"
            "def upgrade():\n    pass\n\n"
            "def downgrade():\n    pass\n"
        )
    return rev_id


def upgrade_to_head(migrations_dir: str = MIGRATIONS_DIR):
    cfg = _alembic_config(migrations_dir)
    command.upgrade(cfg, "head")


def ensure_single_head(migrations_dir: str = MIGRATIONS_DIR, auto_merge: bool = False):
    heads = get_heads(migrations_dir)
    if len(heads) > 1:
        if auto_merge:
            merge_heads(heads, migrations_dir)
        else:
            raise RuntimeError(
                f"Multiple migration heads detected: {heads}. "
                f"Run 'python backend/check_migrations.py --merge' to merge."
            )
    return heads[0] if heads else None


def get_health(migrations_dir: str = MIGRATIONS_DIR) -> dict:
    cfg = _alembic_config(migrations_dir)
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    current = script.get_current_head()
    return {
        "current": current,
        "heads": list(heads),
        "multiple_heads": len(heads) > 1,
        "up_to_date": current in heads,
    }


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


def detect_model_changes(migrations_dir: str = MIGRATIONS_DIR) -> dict:
    cfg = _alembic_config(migrations_dir)
    database_url = cfg.get_main_option("sqlalchemy.url")
    engine = create_engine(database_url)

    result = {
        "has_changes": False,
        "changes": [],
        "summary": "No model changes detected",
    }

    try:
        from backend.models import db

        with engine.connect() as conn:
            context = MigrationContext.configure(conn)

            diff = compare_metadata(context, db.metadata)

            if diff:
                result["has_changes"] = True
                result["changes"] = []

                add_columns = []
                remove_columns = []
                add_tables = []
                remove_tables = []
                modify_columns = []
                other_changes = []

                for change in diff:
                    change_type = change[0]

                    if change_type == "add_column":
                        table_name = (
                            change[2].name
                            if hasattr(change[2], "name")
                            else str(change[2])
                        )
                        col_name = (
                            change[3].name
                            if hasattr(change[3], "name")
                            else str(change[3])
                        )
                        add_columns.append(f"{table_name}.{col_name}")
                        result["changes"].append(
                            {
                                "type": "add_column",
                                "table": table_name,
                                "column": col_name,
                            }
                        )
                    elif change_type == "remove_column":
                        table_name = (
                            change[2].name
                            if hasattr(change[2], "name")
                            else str(change[2])
                        )
                        col_name = (
                            change[3].name
                            if hasattr(change[3], "name")
                            else str(change[3])
                        )
                        remove_columns.append(f"{table_name}.{col_name}")
                        result["changes"].append(
                            {
                                "type": "remove_column",
                                "table": table_name,
                                "column": col_name,
                            }
                        )
                    elif change_type == "add_table":
                        table_name = (
                            change[1].name
                            if hasattr(change[1], "name")
                            else str(change[1])
                        )
                        add_tables.append(table_name)
                        result["changes"].append(
                            {
                                "type": "add_table",
                                "table": table_name,
                            }
                        )
                    elif change_type == "remove_table":
                        table_name = (
                            change[1].name
                            if hasattr(change[1], "name")
                            else str(change[1])
                        )
                        remove_tables.append(table_name)
                        result["changes"].append(
                            {
                                "type": "remove_table",
                                "table": table_name,
                            }
                        )
                    elif (
                        change_type == "modify_type" or change_type == "modify_nullable"
                    ):
                        modify_columns.append(str(change))
                        result["changes"].append(
                            {
                                "type": change_type,
                                "details": str(change),
                            }
                        )
                    else:
                        other_changes.append(str(change))
                        result["changes"].append(
                            {
                                "type": change_type,
                                "details": str(change),
                            }
                        )

                parts = []
                if add_columns:
                    parts.append(f"add columns: {', '.join(add_columns)}")
                if remove_columns:
                    parts.append(f"remove columns: {', '.join(remove_columns)}")
                if add_tables:
                    parts.append(f"add tables: {', '.join(add_tables)}")
                if remove_tables:
                    parts.append(f"remove tables: {', '.join(remove_tables)}")
                if modify_columns:
                    parts.append(f"modify columns: {len(modify_columns)}")
                if other_changes:
                    parts.append(f"other changes: {len(other_changes)}")

                result["summary"] = (
                    "; ".join(parts) if parts else "Model changes detected"
                )

    except ImportError as e:
        result["error"] = f"Could not import models: {e}"
    except Exception as e:
        result["error"] = f"Error detecting changes: {e}"
    finally:
        engine.dispose()

    return result


def get_pending_migrations(migrations_dir: str = MIGRATIONS_DIR) -> list:
    cfg = _alembic_config(migrations_dir)
    script = ScriptDirectory.from_config(cfg)

    db_revision = get_database_revision(migrations_dir)

    if db_revision is None:
        return [rev.revision for rev in script.walk_revisions()]

    pending = []
    for rev in script.walk_revisions():
        if rev.revision == db_revision:
            break
        pending.append(rev.revision)

    return pending


def get_comprehensive_health(migrations_dir: str = MIGRATIONS_DIR) -> dict:
    health = get_health(migrations_dir)

    health["db_revision"] = get_database_revision(migrations_dir)

    pending = get_pending_migrations(migrations_dir)
    health["pending_migrations"] = pending
    health["has_pending"] = len(pending) > 0

    model_diff = detect_model_changes(migrations_dir)
    health["model_changes"] = model_diff
    health["has_model_changes"] = model_diff.get("has_changes", False)

    if health["multiple_heads"]:
        health["status"] = "multiple_heads"
        health["action_needed"] = "merge"
    elif health["has_pending"]:
        health["status"] = "pending"
        health["action_needed"] = "upgrade"
    elif health["has_model_changes"]:
        health["status"] = "model_changes"
        health["action_needed"] = "migrate"
    else:
        health["status"] = "ok"
        health["action_needed"] = None

    return health


def auto_migrate(
    migrations_dir: str = MIGRATIONS_DIR, message: str = "auto migration"
) -> dict:
    model_diff = detect_model_changes(migrations_dir)

    if not model_diff.get("has_changes", False):
        return {
            "success": True,
            "revision": None,
            "message": "No model changes detected - nothing to migrate",
        }

    cfg = _alembic_config(migrations_dir)

    stdout_capture = StringIO()
    stderr_capture = StringIO()

    try:
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            command.revision(cfg, message=message, autogenerate=True)

        script = ScriptDirectory.from_config(cfg)
        new_head = script.get_current_head()

        return {
            "success": True,
            "revision": new_head,
            "message": f"Migration created: {new_head}",
            "changes": model_diff.get("summary", ""),
        }
    except Exception as e:
        return {
            "success": False,
            "revision": None,
            "message": f"Failed to create migration: {e}",
            "stdout": stdout_capture.getvalue(),
            "stderr": stderr_capture.getvalue(),
        }


def auto_upgrade(migrations_dir: str = MIGRATIONS_DIR) -> dict:
    pending = get_pending_migrations(migrations_dir)

    if not pending:
        return {
            "success": True,
            "message": "No pending migrations",
            "applied": [],
        }

    cfg = _alembic_config(migrations_dir)

    try:
        command.upgrade(cfg, "head")

        return {
            "success": True,
            "message": f"Applied {len(pending)} migration(s)",
            "applied": pending,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to apply migrations: {e}",
            "applied": [],
        }
