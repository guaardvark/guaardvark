#!/usr/bin/env python3
"""
Pre-flight schema check for start.sh

Checks database schema status and outputs JSON for bash parsing.

Exit codes:
  0 = OK, database schema is in sync
  1 = Schema needs sync (needs stamp/create_all)
  3 = Database connection error
  4 = Other error
"""
import json
import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
backend_dir = os.path.join(project_root, "backend")
sys.path.insert(0, project_root)
sys.path.insert(0, backend_dir)


def output_result(status, message, fix=None, details=None):
    result = {"status": status, "message": message}
    if fix:
        result["fix"] = fix
    if details:
        result["details"] = details
    print(json.dumps(result, default=str))
    exit_codes = {"ok": 0, "needs_stamp": 1, "multiple_heads": 1, "pending": 2,
                  "connection_error": 3, "error": 4, "model_changes": 5}
    return exit_codes.get(status, 4)


def main():
    migrations_dir = os.path.join(backend_dir, "migrations")
    if not os.path.isdir(migrations_dir):
        return output_result("ok", "No migrations directory found - skipping check")

    try:
        from backend.utils.migration_utils import get_comprehensive_health
        health = get_comprehensive_health(migrations_dir)
        status = health.get("status")

        if status == "ok":
            return output_result("ok",
                f"Database schema up to date (head: {health.get('current', 'unknown')})",
                details=health)

        if status == "needs_stamp":
            return output_result("pending",
                "Database needs re-stamp to current schema head",
                fix="Run: python3 scripts/schema_sync.py",
                details=health)

        if status == "multiple_heads":
            return output_result("multiple_heads",
                f"Multiple migration heads: {health.get('heads', [])}",
                fix="This should not happen -- check backend/migrations/versions/",
                details=health)

        return output_result("ok",
            f"Schema status: {status} (head: {health.get('current', 'unknown')})",
            details=health)

    except Exception as e:
        error_msg = str(e)
        if "connection" in error_msg.lower() or "database" in error_msg.lower():
            return output_result("connection_error", f"Database connection error: {error_msg}")
        return output_result("error", f"Schema check failed: {error_msg}")


if __name__ == "__main__":
    sys.exit(main())
