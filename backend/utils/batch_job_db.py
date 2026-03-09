import csv
import json
import logging
import os
import re
from typing import Any, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from . import progress_manager

logger = logging.getLogger(__name__)


# ============================================================================
# Database connection (PostgreSQL via SQLAlchemy)
# ============================================================================

def _get_database_url():
    url = os.environ.get('DATABASE_URL')
    if url:
        return url
    return "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"

_engine = None
_SessionFactory = None

def _get_db_session():
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_engine(_get_database_url(), pool_pre_ping=True)
        _SessionFactory = sessionmaker(bind=_engine)
    return _SessionFactory()


def _ensure_tables():
    """Create the batch job tables if they don't exist."""
    session = _get_db_session()
    try:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS batch_job_rows (
                id SERIAL PRIMARY KEY,
                job_id TEXT NOT NULL,
                row_data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_batch_job_rows_job_id
            ON batch_job_rows(job_id)
        """))
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS batch_job_columns (
                id SERIAL PRIMARY KEY,
                job_id TEXT UNIQUE NOT NULL,
                columns JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _sanitize_column_name(column_name: str) -> str:
    """Sanitize column name to prevent SQL injection.

    Only allows alphanumeric characters and underscores.
    Truncates to 64 characters maximum.
    """
    if not isinstance(column_name, str):
        raise ValueError("Column name must be a string")

    # Remove any characters that aren't alphanumeric or underscore
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', column_name)

    # Ensure it starts with a letter or underscore
    if sanitized and sanitized[0].isdigit():
        sanitized = '_' + sanitized

    # Truncate to reasonable length
    sanitized = sanitized[:64]

    # Provide fallback if empty
    if not sanitized:
        sanitized = 'col_unnamed'

    return sanitized


def _validate_columns(columns: List[str]) -> List[str]:
    """Validate and sanitize column names."""
    if not columns:
        raise ValueError("Column list cannot be empty")

    sanitized_columns = []
    seen_names = set()

    for i, col in enumerate(columns):
        sanitized = _sanitize_column_name(col)

        # Handle duplicates
        original_sanitized = sanitized
        counter = 1
        while sanitized in seen_names:
            sanitized = f"{original_sanitized}_{counter}"
            counter += 1

        seen_names.add(sanitized)
        sanitized_columns.append(sanitized)

        if sanitized != col:
            logger.warning(
                "Column name '%s' sanitized to '%s' for security",
                col, sanitized
            )

    return sanitized_columns


def init_db(output_dir: str, job_id: str, columns: List[str]) -> str:
    """Initialize batch job storage for the given job with the specified columns.

    The output_dir parameter is kept for API compatibility but is no longer used
    for database path resolution (data is stored in PostgreSQL).
    Returns a string identifier for the job.
    """
    _ensure_tables()

    # Sanitize column names for security
    sanitized_columns = _validate_columns(columns)

    session = _get_db_session()
    try:
        # Store the column definitions for this job
        session.execute(text("""
            INSERT INTO batch_job_columns (job_id, columns)
            VALUES (:job_id, :columns)
            ON CONFLICT (job_id) DO UPDATE SET columns = EXCLUDED.columns
        """), {
            "job_id": job_id,
            "columns": json.dumps(sanitized_columns)
        })
        session.commit()
        logger.info("Initialised batch job storage for job %s", job_id)
        return job_id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def insert_row(
    output_dir: str, job_id: str, columns: List[str], row_values: List[Any]
) -> None:
    """Insert a single row of data for the job.

    The output_dir parameter is kept for API compatibility but is no longer used
    for database path resolution.
    """
    _ensure_tables()

    # Sanitize column names for security
    sanitized_columns = _validate_columns(columns)

    # Build a dict mapping column names to values
    row_data = {}
    for col, val in zip(sanitized_columns, row_values):
        row_data[col] = val

    session = _get_db_session()
    try:
        session.execute(text("""
            INSERT INTO batch_job_rows (job_id, row_data)
            VALUES (:job_id, :row_data)
        """), {
            "job_id": job_id,
            "row_data": json.dumps(row_data)
        })
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def export_to_csv(
    output_dir: str,
    job_id: str,
    output_path: str,
    columns: List[str],
    row_limit: Optional[int] = None,
    size_limit_bytes: Optional[int] = None,
) -> List[str]:
    """Dump all rows to one or more CSV files and return the file paths.

    The output_dir parameter is kept for API compatibility but is no longer used
    for database path resolution.
    """
    _ensure_tables()

    # Sanitize column names for security
    sanitized_columns = _validate_columns(columns)

    session = _get_db_session()
    try:
        result = session.execute(text("""
            SELECT row_data FROM batch_job_rows
            WHERE job_id = :job_id
            ORDER BY id
        """), {"job_id": job_id})

        all_rows = result.fetchall()
    finally:
        session.close()

    base, ext = os.path.splitext(output_path)
    if not ext:
        ext = ".csv"
    file_index = 1
    current_path = (
        output_path
        if row_limit is None and size_limit_bytes is None
        else f"{base}_{file_index}{ext}"
    )
    os.makedirs(os.path.dirname(current_path), exist_ok=True)
    csvfile = open(current_path, "w", newline="", encoding="utf-8")
    writer = csv.writer(csvfile, quoting=csv.QUOTE_ALL)
    writer.writerow(columns)
    row_count = 0
    paths: List[str] = []

    for db_row in all_rows:
        row_data = db_row[0] if isinstance(db_row[0], dict) else json.loads(db_row[0])
        # Extract values in the order of sanitized columns
        csv_row = [row_data.get(c, "") for c in sanitized_columns]
        writer.writerow(csv_row)
        row_count += 1
        rotate = False
        if row_limit and row_count >= row_limit:
            rotate = True
        if size_limit_bytes and csvfile.tell() >= size_limit_bytes:
            rotate = True
        if rotate:
            csvfile.close()
            paths.append(current_path)
            file_index += 1
            current_path = f"{base}_{file_index}{ext}"
            csvfile = open(current_path, "w", newline="", encoding="utf-8")
            writer = csv.writer(csvfile, quoting=csv.QUOTE_ALL)
            writer.writerow(columns)
            row_count = 0

    csvfile.close()
    paths.append(current_path)
    logger.info("Exported %d CSV file(s) from batch DB", len(paths))
    return paths


def cleanup_db(output_dir: str, job_id: str) -> None:
    """Remove all data for a batch job.

    The output_dir parameter is kept for API compatibility but is no longer used
    for database path resolution.
    """
    _ensure_tables()

    session = _get_db_session()
    try:
        session.execute(text("""
            DELETE FROM batch_job_rows WHERE job_id = :job_id
        """), {"job_id": job_id})
        session.execute(text("""
            DELETE FROM batch_job_columns WHERE job_id = :job_id
        """), {"job_id": job_id})
        session.commit()
        logger.info("Removed batch data for job %s", job_id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
