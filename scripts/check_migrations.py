#!/usr/bin/env python3
"""
Pre-flight migration check for start.sh

Checks database migration status and outputs JSON for bash parsing.

Exit codes:
  0 = OK, database ready
  1 = Multiple heads detected (needs merge)
  2 = Migrations pending (needs upgrade)
  3 = Database connection error
  4 = Other error
"""

import json
import os
import sys

# Add project paths
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
backend_dir = os.path.join(project_root, "backend")
sys.path.insert(0, project_root)
sys.path.insert(0, backend_dir)


def output_result(status, message, fix=None, details=None):
    """Output JSON result and return appropriate exit code."""
    result = {
        "status": status,
        "message": message,
    }
    if fix:
        result["fix"] = fix
    if details:
        result["details"] = details

    print(json.dumps(result, default=str))

    exit_codes = {
        "ok": 0,
        "multiple_heads": 1,
        "pending": 2,
        "model_changes": 5,
        "connection_error": 3,
        "error": 4,
    }
    return exit_codes.get(status, 4)


def main():
    migrations_dir = os.path.join(backend_dir, "migrations")

    # Check if migrations directory exists
    if not os.path.isdir(migrations_dir):
        return output_result("ok", "No migrations directory found - skipping check")

    try:
        from backend.utils.migration_utils import get_comprehensive_health

        # Get comprehensive migration health
        health = get_comprehensive_health(migrations_dir)
        status = health.get("status")

        if status == "multiple_heads":
            return output_result(
                "multiple_heads",
                f"Multiple migration heads detected: {', '.join(health.get('heads', []))}",
                fix="Run: cd backend && flask db merge heads -m 'merge heads'",
                details=health,
            )

        if status == "pending":
            return output_result(
                "pending",
                f"Database migrations are pending ({len(health.get('pending_migrations', []))} revision(s))",
                fix="Run: cd backend && flask db upgrade",
                details=health,
            )

        if status == "model_changes":
            return output_result(
                "model_changes",
                f"Model changes detected: {health.get('model_changes', {}).get('summary')}",
                fix="Run: cd backend && flask db migrate -m 'auto migration' && flask db upgrade",
                details=health,
            )

        # All good
        return output_result(
            "ok",
            f"Database migrations up to date (head: {health.get('current', 'unknown')})",
            details=health,
        )

    except ImportError as e:
        return output_result(
            "error",
            f"Import error - migration utils not found: {e}",
            fix="Ensure backend dependencies are installed",
        )

    except Exception as e:
        error_msg = str(e)
        if "connection" in error_msg.lower() or "database" in error_msg.lower():
            return output_result(
                "connection_error",
                f"Database connection error: {error_msg}",
                fix="Check database configuration and ensure database file/server is accessible",
            )
        return output_result("error", f"Migration check failed: {error_msg}")


if __name__ == "__main__":
    sys.exit(main())
