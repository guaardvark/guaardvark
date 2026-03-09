# PostgreSQL Auto-Setup Design

**Date:** 2026-03-01
**Status:** Approved

## Goal

Replace SQLite with PostgreSQL as the sole database backend for Guaardvark. The `start.sh` script auto-provisions PostgreSQL (install, configure, create database/user) so users never think about database setup.

## Decisions

- **One database**: Single `guaardvark` PostgreSQL database for everything (dev, tests, production)
- **No SQLite**: Remove all SQLite-specific code paths
- **Auto-provision**: `start.sh` installs PostgreSQL via `apt`, starts the service, creates DB/user, generates credentials
- **Credentials**: Auto-generated password stored in `.env` as `DATABASE_URL`
- **Advanced override**: Users can set `DATABASE_URL` in `.env` to point at any PostgreSQL instance

## Architecture

### start.sh PostgreSQL Phase

New `setup_postgresql()` function runs between Redis setup and migration checks:

1. Check if `psql` command exists; if not, `sudo apt-get install -y postgresql postgresql-contrib`
2. Ensure `postgresql` service is active; if not, `sudo systemctl start postgresql && sudo systemctl enable postgresql`
3. Read `DATABASE_URL` from `.env`; if already set and valid, skip provisioning
4. Generate password via `openssl rand -base64 24`
5. Create PostgreSQL user `guaardvark` with the generated password (idempotent)
6. Create database `guaardvark` owned by user `guaardvark` (idempotent)
7. Write `DATABASE_URL=postgresql://guaardvark:<password>@localhost:5432/guaardvark` to `.env`
8. Verify connection with `psql -c "SELECT 1;"`

### backend/config.py

- Default `DATABASE_URL` reads from environment/`.env`
- Fallback: `postgresql://guaardvark:guaardvark@localhost:5432/guaardvark`
- Remove `DATABASE_PATH`, `:memory:` logic, SQLite-specific paths
- Remove `GUAARDVARK_MODE=test` in-memory database override

### backend/models.py

- Remove `_set_sqlite_pragmas` event listener (PRAGMA is SQLite-only)
- Convert partial index from `sqlite_where=text(...)` to `postgresql_where=text(...)`

### backend/migrations/env.py

- Keep `render_as_batch=True` (harmless for PostgreSQL)
- Ensure `DATABASE_URL` resolution matches config.py

### Direct sqlite3 Refactoring

Three files use raw `sqlite3` connections bypassing SQLAlchemy:

| File | Current Usage | Change |
|------|--------------|--------|
| `backend/celery_tasks_isolated.py` | Direct sqlite3 for document indexing | Refactor to SQLAlchemy |
| `backend/tasks/task_scheduler_celery.py` | Direct sqlite3 for task scheduling | Refactor to SQLAlchemy |
| `backend/tasks/unified_task_executor.py` | Direct sqlite3 for task execution | Refactor to SQLAlchemy |

### backend/tests/conftest.py

- Remove in-memory SQLite configuration
- Tests run against the main `guaardvark` database

### No Changes Required

- `backend/requirements.txt` — already has `psycopg2-binary==2.9.9`
- `stop.sh` — PostgreSQL runs as a system service (like Redis)

## File Change Summary

| File | Change Type |
|------|------------|
| `start.sh` | Add PostgreSQL auto-provision phase |
| `.env` | `DATABASE_URL` auto-populated |
| `backend/config.py` | Default to PG, remove SQLite paths |
| `backend/models.py` | Remove SQLite pragmas, fix partial index |
| `backend/migrations/env.py` | Ensure PG URL resolution |
| `backend/celery_tasks_isolated.py` | Replace sqlite3 with SQLAlchemy |
| `backend/tasks/task_scheduler_celery.py` | Replace sqlite3 with SQLAlchemy |
| `backend/tasks/unified_task_executor.py` | Replace sqlite3 with SQLAlchemy |
| `backend/tests/conftest.py` | Remove in-memory SQLite config |
