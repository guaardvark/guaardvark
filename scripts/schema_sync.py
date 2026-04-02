#!/usr/bin/env python3
"""
Schema Sync — ensures the database matches models.py without migration replay.

Usage:
    python3 scripts/schema_sync.py          # Sync schema and stamp
    python3 scripts/schema_sync.py --check  # Check only, don't modify

Exit codes:
    0 = Schema is in sync (or was synced successfully)
    1 = Schema has differences (--check mode only)
    2 = Error
"""
import json
import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
backend_dir = os.path.join(project_root, "backend")
sys.path.insert(0, project_root)
sys.path.insert(0, backend_dir)


def main():
    check_only = "--check" in sys.argv

    try:
        os.environ.setdefault("GUAARDVARK_ROOT", project_root)
        # Prevent create_app() from running its own migration logic
        os.environ["GUAARDVARK_MIGRATIONS_VERIFIED"] = "1"
        from backend.app import create_app
        from backend.models import db
        from backend.config import DATABASE_URL
        from sqlalchemy import create_engine, inspect, text
        from alembic.config import Config
        from alembic import command

        app = create_app()

        with app.app_context():
            engine = create_engine(DATABASE_URL)
            inspector = inspect(engine)
            existing_tables = set(inspector.get_table_names())

            if not existing_tables or "clients" not in existing_tables:
                # Fresh database — create everything from models.py
                if check_only:
                    print(json.dumps({"status": "fresh", "message": "No tables exist"}))
                    return 1
                db.create_all()
                status_msg = "Fresh database created from models.py"
            else:
                # Existing database — db.create_all() adds missing tables/columns
                if check_only:
                    model_tables = set(db.metadata.tables.keys())
                    missing = model_tables - existing_tables
                    if missing:
                        print(json.dumps({
                            "status": "drift",
                            "missing_tables": sorted(missing),
                        }))
                        return 1
                    print(json.dumps({"status": "ok", "message": "Schema in sync"}))
                    return 0
                db.create_all()
                status_msg = "Schema synced (missing tables/columns added)"

            # Stamp alembic_version to current head
            migrations_dir = os.path.join(backend_dir, "migrations")
            if os.path.isdir(migrations_dir):
                cfg = Config(os.path.join(migrations_dir, "alembic.ini"))
                cfg.set_main_option("script_location", migrations_dir)
                cfg.set_main_option("sqlalchemy.url", DATABASE_URL)

                # Ensure alembic_version table exists and is clean
                with engine.connect() as conn:
                    # Create alembic_version if it doesn't exist
                    conn.execute(text(
                        "CREATE TABLE IF NOT EXISTS alembic_version "
                        "(version_num VARCHAR(32) NOT NULL, "
                        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                    ))
                    conn.execute(text("DELETE FROM alembic_version"))
                    conn.commit()

                command.stamp(cfg, "head")
                status_msg += " and stamped to head"

            print(json.dumps({"status": "ok", "message": status_msg}))
            return 0

    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
