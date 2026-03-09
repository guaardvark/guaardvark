# PostgreSQL Auto-Setup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace SQLite with PostgreSQL as the sole database backend, with fully automatic provisioning in start.sh.

**Architecture:** Add a PostgreSQL setup phase to start.sh that installs, configures, and provisions the database. Refactor all direct sqlite3 connections in Celery workers to use SQLAlchemy with the PostgreSQL connection string from DATABASE_URL. Remove all SQLite-specific code from config and models.

**Tech Stack:** PostgreSQL (via apt), psycopg2-binary (already in requirements.txt), SQLAlchemy, Alembic

---

### Task 1: Create `start_postgres.sh` Script

**Files:**
- Create: `start_postgres.sh`

This follows the exact same pattern as `start_redis.sh` (see `/home/llamax1/LLAMAX7/start_redis.sh` for reference).

**Step 1: Write start_postgres.sh**

```bash
#!/bin/bash
# start_postgres.sh - auto-provision PostgreSQL for Guaardvark

VADER_RED="\033[38;5;196m"
VADER_RED_DARK="\033[38;5;88m"
VADER_RED_LIGHT="\033[38;5;203m"
VADER_GRAY="\033[38;5;244m"
VADER_GRAY_DARK="\033[38;5;238m"
VADER_WHITE="\033[38;5;255m"
VADER_WHITE_DIM="\033[38;5;250m"
VADER_RESET="\033[0m"
VADER_BOLD="\033[1m"

vader_info() { echo -e "  ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_success() { echo -e "  ${VADER_RED}✔${VADER_RESET} ${VADER_WHITE}$1${VADER_RESET}"; }
vader_warn() { echo -e "  ${VADER_RED_LIGHT}⚠${VADER_RESET} ${VADER_RED_LIGHT}$1${VADER_RESET}"; }
vader_error() { echo -e "  ${VADER_RED_DARK}✖${VADER_RESET} ${VADER_RED}$1${VADER_RESET}"; }

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
ENV_FILE="$SCRIPT_DIR/.env"

# ── 1. Check if DATABASE_URL is already configured and valid ──
if [ -f "$ENV_FILE" ]; then
    EXISTING_URL=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-)
    if [ -n "$EXISTING_URL" ]; then
        # Verify connection works
        if PGPASSWORD=$(echo "$EXISTING_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p') \
           psql "$(echo "$EXISTING_URL" | sed 's|postgresql://|postgres://|')" -c "SELECT 1;" >/dev/null 2>&1; then
            vader_success "PostgreSQL already configured and connected."
            exit 0
        else
            vader_warn "DATABASE_URL in .env but connection failed. Re-provisioning..."
        fi
    fi
fi

# ── 2. Check if PostgreSQL is installed ──
if ! command -v psql >/dev/null 2>&1; then
    vader_info "PostgreSQL not found. Installing..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y -qq postgresql postgresql-contrib >/dev/null 2>&1
        if [ $? -ne 0 ]; then
            vader_error "Failed to install PostgreSQL via apt."
            exit 1
        fi
        vader_success "PostgreSQL installed."
    else
        vader_error "apt-get not found. Install PostgreSQL manually."
        exit 1
    fi
else
    vader_success "PostgreSQL is installed."
fi

# ── 3. Ensure PostgreSQL service is running ──
if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl is-active --quiet postgresql; then
        vader_info "Starting PostgreSQL service..."
        sudo systemctl start postgresql
        sudo systemctl enable postgresql >/dev/null 2>&1
        sleep 2
        if ! systemctl is-active --quiet postgresql; then
            vader_error "Failed to start PostgreSQL service."
            exit 1
        fi
    fi
    vader_success "PostgreSQL service is running."
else
    # Fallback: check if postgres process is running
    if ! pgrep -x postgres >/dev/null 2>&1; then
        vader_error "PostgreSQL not running and systemctl not available."
        exit 1
    fi
    vader_success "PostgreSQL process is running."
fi

# ── 4. Generate credentials and create database ──
PG_USER="guaardvark"
PG_DB="guaardvark"

# Generate password (only if we need to create the user)
PG_PASS=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)

# Check if user exists
USER_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'" 2>/dev/null)

if [ "$USER_EXISTS" != "1" ]; then
    vader_info "Creating PostgreSQL user '$PG_USER'..."
    sudo -u postgres psql -c "CREATE USER $PG_USER WITH PASSWORD '$PG_PASS';" >/dev/null 2>&1
    if [ $? -ne 0 ]; then
        vader_error "Failed to create PostgreSQL user."
        exit 1
    fi
    vader_success "PostgreSQL user '$PG_USER' created."
else
    vader_info "PostgreSQL user '$PG_USER' already exists."
    # Reset password to our generated one so we can write it to .env
    sudo -u postgres psql -c "ALTER USER $PG_USER WITH PASSWORD '$PG_PASS';" >/dev/null 2>&1
fi

# Check if database exists
DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" 2>/dev/null)

if [ "$DB_EXISTS" != "1" ]; then
    vader_info "Creating database '$PG_DB'..."
    sudo -u postgres psql -c "CREATE DATABASE $PG_DB OWNER $PG_USER;" >/dev/null 2>&1
    if [ $? -ne 0 ]; then
        vader_error "Failed to create database."
        exit 1
    fi
    vader_success "Database '$PG_DB' created."
else
    vader_success "Database '$PG_DB' already exists."
    # Ensure ownership
    sudo -u postgres psql -c "ALTER DATABASE $PG_DB OWNER TO $PG_USER;" >/dev/null 2>&1
fi

# ── 5. Write DATABASE_URL to .env ──
DATABASE_URL="postgresql://$PG_USER:$PG_PASS@localhost:5432/$PG_DB"

# Remove old DATABASE_URL line if present, then append new one
if [ -f "$ENV_FILE" ]; then
    grep -v '^DATABASE_URL=' "$ENV_FILE" > "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"
fi
echo "DATABASE_URL=$DATABASE_URL" >> "$ENV_FILE"
vader_success "DATABASE_URL written to .env"

# ── 6. Verify connection ──
if PGPASSWORD="$PG_PASS" psql -h localhost -U "$PG_USER" -d "$PG_DB" -c "SELECT 1;" >/dev/null 2>&1; then
    vader_success "PostgreSQL connection verified."
else
    vader_error "PostgreSQL connection verification failed."
    exit 1
fi

exit 0
```

**Step 2: Make it executable**

Run: `chmod +x start_postgres.sh`

**Step 3: Commit**

```bash
git add start_postgres.sh
git commit -m "feat: add PostgreSQL auto-provision script"
```

---

### Task 2: Integrate PostgreSQL Setup into `start.sh`

**Files:**
- Modify: `start.sh` (lines ~1009-1013, where Redis step is; add PostgreSQL step before it)

**Step 1: Add PostgreSQL step to start.sh**

Insert a new step after the Redis step (step 5) by bumping `TOTAL_STEPS` from 10 to 11, and adding the PostgreSQL step. The new step goes right after the Redis step and before the Whisper step.

Changes needed:
1. Line 24: Change `TOTAL_STEPS=10` to `TOTAL_STEPS=11`
2. After the Redis step (line ~1012), add:

```bash
vader_step 6 "Ensuring PostgreSQL database is ready..."
"$(dirname "$0")/start_postgres.sh" || { vader_error "PostgreSQL setup failed"; exit 1; }
vader_separator
```

3. Renumber all subsequent steps: 6→7, 7→8, 8→9, 9→10, 10→11

**Step 2: Commit**

```bash
git add start.sh
git commit -m "feat: add PostgreSQL auto-setup step to start.sh"
```

---

### Task 3: Update `backend/config.py` — Default to PostgreSQL

**Files:**
- Modify: `backend/config.py`

**Step 1: Rewrite the database configuration section**

Replace lines 46 (DATABASE_PATH) through 139 (the warning block) with:

```python
# PostgreSQL is the default database.
# DATABASE_URL is set automatically by start_postgres.sh in .env,
# or can be overridden manually for advanced setups.
_DEFAULT_DATABASE_URL = "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"

_env_db_url = os.environ.get("DATABASE_URL")
if _env_db_url:
    allowed_schemes = ["postgresql", "postgres"]
    if any(_env_db_url.startswith(f"{scheme}://") for scheme in allowed_schemes):
        DATABASE_URL = _env_db_url
        _config_logger.info(f"Using DATABASE_URL from environment: {_env_db_url[:50]}...")
    else:
        _config_logger.warning(
            f"DATABASE_URL has unsupported scheme: {_env_db_url[:20]}... "
            f"Falling back to default PostgreSQL."
        )
        DATABASE_URL = _DEFAULT_DATABASE_URL
else:
    DATABASE_URL = _DEFAULT_DATABASE_URL
    _config_logger.info(f"Using default DATABASE_URL: {DATABASE_URL[:50]}...")
```

Also remove:
- Line 46: `DATABASE_PATH = str(...)` — remove entirely
- Lines 100-111: The `if GUAARDVARK_MODE == "test":` block that sets `DATABASE_PATH = ":memory:"` — remove the `DATABASE_PATH = ":memory:"` line (keep the tmp dir lines for UPLOAD_DIR etc. if tests still use those)
- Lines 135-139: The `DATABASE_PATH` validation warnings — remove entirely

Note: `DATABASE_PATH` is removed as a module-level export. Files that import it will be fixed in subsequent tasks.

**Step 2: Run the test suite to see what breaks**

Run: `cd /home/llamax1/LLAMAX7 && python3 -m pytest backend/tests -x -q 2>&1 | head -30`
Expected: Some tests may fail due to missing DATABASE_PATH import — that's expected and will be fixed in subsequent tasks.

**Step 3: Commit**

```bash
git add backend/config.py
git commit -m "feat: default database config to PostgreSQL, remove SQLite paths"
```

---

### Task 4: Update `backend/models.py` — Remove SQLite Pragmas, Fix Partial Index

**Files:**
- Modify: `backend/models.py`

**Step 1: Remove the sqlite3 import and pragma listener**

Remove `import sqlite3` (line 8) and the entire `_set_sqlite_pragmas` function (lines 33-45).

**Step 2: Convert sqlite_where to postgresql_where**

Find line 333:
```python
sqlite_where=text("is_active = 1 AND name != 'qa_default'"),
```

Replace with:
```python
postgresql_where=text("is_active = 1 AND name != 'qa_default'"),
```

**Step 3: Commit**

```bash
git add backend/models.py
git commit -m "feat: remove SQLite pragmas, convert partial index to postgresql_where"
```

---

### Task 5: Update `backend/migrations/env.py` — Clean Up Comments

**Files:**
- Modify: `backend/migrations/env.py`

**Step 1: Update env.py**

The file currently works fine because it reads `DATABASE_URL` from config. Just update the comment on line 52 and keep `render_as_batch=True` (harmless).

Change line 52 comment from:
```python
        # render_as_batch=True enables SQLite-compatible ALTER TABLE operations
        # (SQLite doesn't support ALTER of constraints natively)
```
to:
```python
        # render_as_batch=True kept for migration compatibility
```

Also update the fallback on line 22 from `sqlite:///alembic.db` to the PostgreSQL default:
```python
        config.set_main_option("sqlalchemy.url", "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark")
```

**Step 2: Commit**

```bash
git add backend/migrations/env.py
git commit -m "feat: update migration env.py comments and fallback for PostgreSQL"
```

---

### Task 6: Refactor `backend/celery_tasks_isolated.py` — Replace sqlite3 with SQLAlchemy

**Files:**
- Modify: `backend/celery_tasks_isolated.py`

This is the largest refactor. The file currently uses direct `sqlite3.connect()` to avoid Flask dependencies. We replace this with standalone SQLAlchemy engine/session (no Flask required).

**Step 1: Replace the database connection pattern**

Remove:
```python
import sqlite3
...
DB_PATH = os.environ.get(...)
DATABASE_PATH = os.path.join(...)

def get_db_connection():
    return sqlite3.connect(DATABASE_PATH)
```

Replace with:
```python
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

def _get_database_url():
    """Get DATABASE_URL from environment (set by start_postgres.sh in .env)."""
    url = os.environ.get('DATABASE_URL')
    if url:
        return url
    return "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"

_engine = None
_SessionFactory = None

def get_db_session():
    """Get a SQLAlchemy session for database operations without Flask."""
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_engine(_get_database_url(), pool_pre_ping=True)
        _SessionFactory = sessionmaker(bind=_engine)
    return _SessionFactory()
```

**Step 2: Refactor all functions that use `get_db_connection()`**

Each function follows this pattern — replace `conn = get_db_connection()` / `cursor.execute(...)` / `conn.close()` with `session = get_db_session()` / `session.execute(text(...))` / `session.close()`.

Key changes:
- `?` placeholders become `:param` named parameters
- `cursor.fetchone()` becomes `session.execute(text(...)).fetchone()`
- `conn.commit()` becomes `session.commit()`
- `conn.close()` becomes `session.close()`
- `cursor.lastrowid` becomes `result.lastrowid` (or use `RETURNING id`)

For `get_document_by_id()`:
```python
def get_document_by_id(document_id):
    session = get_db_session()
    try:
        result = session.execute(text("""
            SELECT id, filename, path, project_id, index_status, uploaded_at
            FROM documents
            WHERE id = :doc_id
        """), {"doc_id": document_id})
        row = result.fetchone()
        if row:
            return {
                'id': row[0], 'filename': row[1], 'file_path': row[2],
                'project_id': row[3], 'index_status': row[4], 'uploaded_at': row[5]
            }
        return None
    finally:
        session.close()
```

For `update_document_status()`:
```python
def update_document_status(document_id, status, error_message=None):
    session = get_db_session()
    try:
        if error_message:
            session.execute(text("""
                UPDATE documents
                SET index_status = :status, indexed_at = :now, error_message = :err
                WHERE id = :doc_id
            """), {"status": status, "now": datetime.datetime.now().isoformat(),
                   "err": error_message, "doc_id": document_id})
        else:
            session.execute(text("""
                UPDATE documents
                SET index_status = :status, indexed_at = :now
                WHERE id = :doc_id
            """), {"status": status, "now": datetime.datetime.now().isoformat(),
                   "doc_id": document_id})
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to update document status: {e}")
        return False
    finally:
        session.close()
```

Apply the same pattern to `enhanced_code_aware_indexing()` (the inner `conn = get_db_connection()` block) and `bulk_import_documents_task()` (all sqlite3 usage).

For `bulk_import_documents_task()` — the `cursor.lastrowid` pattern needs to change to use PostgreSQL's `RETURNING`:
```python
result = session.execute(text("""
    INSERT INTO folders (name, path, created_at, updated_at)
    VALUES (:name, :path, :created_at, :updated_at)
    RETURNING id
"""), {...})
folder_id = result.fetchone()[0]
session.commit()
```

**Step 3: Commit**

```bash
git add backend/celery_tasks_isolated.py
git commit -m "refactor: replace sqlite3 with SQLAlchemy in celery_tasks_isolated"
```

---

### Task 7: Refactor `backend/tasks/task_scheduler_celery.py` — Replace sqlite3

**Files:**
- Modify: `backend/tasks/task_scheduler_celery.py`

**Step 1: Apply the same pattern as Task 6**

Replace:
```python
import sqlite3
...
DB_PATH = ...
DATABASE_PATH = ...

def get_db_connection():
    return sqlite3.connect(DATABASE_PATH)
```

With the same `get_db_session()` pattern from Task 6.

Refactor all functions:
- `get_scheduled_tasks()` — replace `?` with `:param`, `cursor` with `session.execute(text(...))`
- `get_stuck_tasks()` — same pattern
- `update_task_for_retry()` — same pattern
- `mark_task_failed()` — same pattern
- The inline `conn = get_db_connection()` in `check_scheduled_tasks()` — same pattern, use `RETURNING` for row count check via `result.rowcount`

**Step 2: Commit**

```bash
git add backend/tasks/task_scheduler_celery.py
git commit -m "refactor: replace sqlite3 with SQLAlchemy in task_scheduler_celery"
```

---

### Task 8: Refactor `backend/tasks/unified_task_executor.py` — Replace sqlite3

**Files:**
- Modify: `backend/tasks/unified_task_executor.py`

**Step 1: Apply the same pattern as Task 6**

Replace the sqlite3 connection with `get_db_session()`.

Refactor:
- `get_task_by_id()` — replace `?` with `:param`
- `update_task_status()` — note the dynamic SQL building; convert to named params
- `update_task_result()` — same pattern

**Step 2: Commit**

```bash
git add backend/tasks/unified_task_executor.py
git commit -m "refactor: replace sqlite3 with SQLAlchemy in unified_task_executor"
```

---

### Task 9: Fix Remaining DATABASE_PATH References

**Files:**
- Modify: `backend/services/simple_indexing_service.py:37-41`
- Modify: `backend/services/task_handlers/system_maintenance_handler.py:396-422`
- Modify: `backend/app.py:320-325`
- Modify: `backend/api/diagnostics_api.py:815,949`

**Step 1: Fix simple_indexing_service.py**

Replace lines 37-41:
```python
from backend.config import DATABASE_PATH
...
database_uri = f"sqlite:///{DATABASE_PATH}"
engine = create_engine(database_uri)
```

With:
```python
from backend.config import DATABASE_URL
...
engine = create_engine(DATABASE_URL)
```

**Step 2: Fix system_maintenance_handler.py**

The VACUUM and ANALYZE commands are SQLite-specific. Replace with PostgreSQL equivalents:
- `VACUUM` → `VACUUM` (PostgreSQL also has VACUUM, but it works differently — it doesn't shrink the file, it reclaims space internally)
- `ANALYZE` → `ANALYZE` (same command in PostgreSQL)
- Remove `os.path.getsize(DATABASE_PATH)` — PostgreSQL doesn't use a file we can measure. Use `pg_database_size()` instead.

Replace the sqlite3 connection block with:
```python
from backend.config import DATABASE_URL
from sqlalchemy import create_engine, text

engine = create_engine(DATABASE_URL)
with engine.connect() as conn:
    conn.execute(text("VACUUM"))
    conn.commit()
...
with engine.connect() as conn:
    conn.execute(text("ANALYZE"))
    conn.commit()
```

For size measurement:
```python
with engine.connect() as conn:
    result = conn.execute(text("SELECT pg_database_size(current_database())"))
    size = result.scalar()
```

**Step 3: Fix app.py**

Remove the `DATABASE_PATH` validation block (lines ~320-325) that checks the SQLite file path. Replace with a PostgreSQL connection test if desired, or simply remove it since `start_postgres.sh` already verifies the connection.

**Step 4: Fix diagnostics_api.py**

Lines 815 and 949 reference `DATABASE_PATH` from Flask config. Update to report the PostgreSQL connection instead of a file path. This is a diagnostic/info endpoint — change it to display the database URL (masked password).

**Step 5: Commit**

```bash
git add backend/services/simple_indexing_service.py backend/services/task_handlers/system_maintenance_handler.py backend/app.py backend/api/diagnostics_api.py
git commit -m "refactor: replace all remaining DATABASE_PATH references with DATABASE_URL"
```

---

### Task 10: Refactor Auxiliary SQLite Files (DLQ, batch_job_db, code_storage_bridge)

**Files:**
- Modify: `backend/utils/task_failure_handling.py`
- Modify: `backend/utils/batch_job_db.py`
- Modify: `backend/utils/code_storage_bridge.py`

These files use separate SQLite databases (not the main system_analysis.db). They need to be migrated to use tables in the main PostgreSQL database.

**Step 1: task_failure_handling.py — Move DLQ to PostgreSQL**

The dead letter queue currently uses `data/database/task_dlq.db`. Replace all `sqlite3.connect(DLQ_DATABASE)` calls with SQLAlchemy sessions using `DATABASE_URL`. The `init_dlq_database()` function that creates the table should use `CREATE TABLE IF NOT EXISTS` via SQLAlchemy `text()`.

Replace the connection pattern throughout (there are ~10 `sqlite3.connect` calls).

**Step 2: batch_job_db.py — Move to PostgreSQL**

This utility accepts a `db_path` parameter and creates sqlite tables. Refactor to accept a `DATABASE_URL` or use the global one, and replace all sqlite3 calls with SQLAlchemy.

Change `?` placeholders to `:param` named parameters throughout.

**Step 3: code_storage_bridge.py — Move to PostgreSQL**

Replace `sqlite3.connect(self.db_path)` with a SQLAlchemy engine using `DATABASE_URL`. Update `__init__` to accept a database URL instead of a file path.

**Step 4: Commit**

```bash
git add backend/utils/task_failure_handling.py backend/utils/batch_job_db.py backend/utils/code_storage_bridge.py
git commit -m "refactor: migrate auxiliary SQLite databases (DLQ, batch_job, code_storage) to PostgreSQL"
```

---

### Task 11: Update `backend/tests/conftest.py`

**Files:**
- Modify: `backend/tests/conftest.py`

**Step 1: Remove in-memory SQLite references**

The conftest.py currently doesn't explicitly set up the in-memory database (that's done in config.py's test mode). But remove the `GUAARDVARK_MODE=test` default if it's still causing SQLite fallback.

Since tests now use the main PostgreSQL database, ensure `DATABASE_URL` environment variable is set (it will be, from .env).

**Step 2: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "refactor: update test config for PostgreSQL"
```

---

### Task 12: Generate a Fresh Alembic Migration for PostgreSQL

**Files:**
- Create: New migration file in `backend/migrations/versions/`

Since the partial index changed from `sqlite_where` to `postgresql_where`, and we're targeting a fresh PostgreSQL database, we need to handle the migration properly.

**Step 1: Start PostgreSQL (if not running)**

Run: `./start_postgres.sh`

**Step 2: Generate migration**

Run:
```bash
cd /home/llamax1/LLAMAX7/backend
source venv/bin/activate
export DATABASE_URL=$(grep DATABASE_URL /home/llamax1/LLAMAX7/.env | cut -d= -f2-)
flask db migrate -m "switch to postgresql"
```

**Step 3: Review and apply migration**

Run: `flask db upgrade`

**Step 4: Verify tables exist**

Run: `PGPASSWORD=<pass> psql -h localhost -U guaardvark -d guaardvark -c "\dt"`

**Step 5: Commit**

```bash
git add backend/migrations/versions/
git commit -m "feat: add migration for PostgreSQL switch"
```

---

### Task 13: End-to-End Verification

**Step 1: Stop everything**

Run: `./stop.sh`

**Step 2: Start fresh**

Run: `./start.sh`
Expected: PostgreSQL auto-provisioned, all services start, migrations applied.

**Step 3: Run tests**

Run: `python3 run_tests.py`
Expected: Tests pass against PostgreSQL.

**Step 4: Manual smoke test**

- Open http://localhost:5175
- Create a project
- Upload a document
- Start a chat conversation
- Verify data persists across restarts

**Step 5: Commit any final fixes**

```bash
git add -A
git commit -m "fix: address issues found during PostgreSQL integration testing"
```

---

### Task 14: Update CLAUDE.md and Documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update CLAUDE.md**

Update the following sections:
- **Technology Stack**: Change "SQLite" to "PostgreSQL" in the Backend section
- **System Requirements**: Add "PostgreSQL 14+" to the list
- **Starting the Application**: Note that PostgreSQL is auto-provisioned
- **Database Migrations**: Update commands for PostgreSQL context
- **Configuration System**: Update DATABASE_PATH references to DATABASE_URL
- **Environment Variables**: Add DATABASE_URL documentation, remove SQLite references
- **Database Schema**: Note PostgreSQL as the database engine

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for PostgreSQL migration"
```
