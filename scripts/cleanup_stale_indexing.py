#!/usr/bin/env python3
"""
Cleanup script for stale indexing jobs and orphaned documents
Addresses issues from testing DocumentsPage with many file operations
"""

import os
import sys
import shutil
import json
from pathlib import Path
from datetime import datetime, timedelta

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, text
from backend.config import DATABASE_URL, OUTPUT_DIR, UPLOAD_DIR

_engine = None


def get_db_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL)
    return _engine


def find_orphaned_documents():
    """Find documents that reference non-existent files"""
    engine = get_db_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, filename, path, index_status, uploaded_at
            FROM documents
            WHERE index_status IN ('INDEXING', 'PENDING', 'ERROR')
        """)).fetchall()

    orphaned = []
    for row in rows:
        doc_id, filename, path, status, uploaded_at = row
        full_path = os.path.join(UPLOAD_DIR, path) if not os.path.isabs(path) else path
        if not os.path.exists(full_path):
            orphaned.append(
                {
                    "id": doc_id,
                    "filename": filename,
                    "path": path,
                    "status": status,
                    "uploaded_at": uploaded_at,
                    "full_path": full_path,
                }
            )

    return orphaned


def find_stale_progress_jobs():
    """Find stale progress job directories"""
    progress_dir = Path(OUTPUT_DIR) / ".progress_jobs"
    if not progress_dir.exists():
        return []

    stale_jobs = []
    cutoff_time = datetime.now() - timedelta(hours=1)  # Jobs older than 1 hour

    for job_dir in progress_dir.iterdir():
        if job_dir.is_dir():
            metadata_file = job_dir / "metadata.json"
            if metadata_file.exists():
                try:
                    with open(metadata_file, "r") as f:
                        metadata = json.load(f)

                    # Check if job is stuck/stale
                    status = metadata.get("status", "unknown")
                    created_at = metadata.get("created_at")

                    if created_at:
                        created_time = datetime.fromisoformat(
                            created_at.replace("Z", "+00:00").replace("+00:00", "")
                        )
                        if created_time < cutoff_time and status not in [
                            "complete",
                            "error",
                        ]:
                            stale_jobs.append(
                                {
                                    "job_id": job_dir.name,
                                    "path": str(job_dir),
                                    "status": status,
                                    "created_at": created_at,
                                    "process_type": metadata.get(
                                        "process_type", "unknown"
                                    ),
                                }
                            )
                except Exception as e:
                    print(f"Error reading {metadata_file}: {e}")

    return stale_jobs


def cleanup_orphaned_documents(orphaned_docs, dry_run=True):
    """Remove orphaned document records from database"""
    if not orphaned_docs:
        print("No orphaned documents to clean up")
        return 0

    if dry_run:
        print(f"\n[DRY RUN] Would remove {len(orphaned_docs)} orphaned documents:")
        for doc in orphaned_docs[:10]:  # Show first 10
            print(
                f"  - ID {doc['id']}: {doc['filename']} (status: {doc['status']}, file missing: {doc['full_path']})"
            )
        if len(orphaned_docs) > 10:
            print(f"  ... and {len(orphaned_docs) - 10} more")
        return 0

    engine = get_db_engine()
    cleaned_count = 0
    with engine.connect() as conn:
        for doc in orphaned_docs:
            try:
                conn.execute(
                    text("DELETE FROM documents WHERE id = :id"), {"id": doc["id"]}
                )
                cleaned_count += 1
            except Exception as e:
                print(f"Error deleting document {doc['id']}: {e}")
        conn.commit()

    print(f"Removed {cleaned_count} orphaned document records")
    return cleaned_count


def cleanup_stale_jobs(stale_jobs, dry_run=True):
    """Remove stale progress job directories"""
    if not stale_jobs:
        print("No stale jobs to clean up")
        return 0

    if dry_run:
        print(f"\n[DRY RUN] Would remove {len(stale_jobs)} stale job directories:")
        for job in stale_jobs[:10]:  # Show first 10
            print(
                f"  - {job['job_id']} ({job['process_type']}, status: {job['status']}, created: {job['created_at']})"
            )
        if len(stale_jobs) > 10:
            print(f"  ... and {len(stale_jobs) - 10} more")
        return 0

    cleaned_count = 0
    for job in stale_jobs:
        try:
            shutil.rmtree(job["path"])
            cleaned_count += 1
        except Exception as e:
            print(f"Error removing {job['path']}: {e}")

    print(f"Removed {cleaned_count} stale job directories")
    return cleaned_count


def reset_stuck_documents(dry_run=True):
    """Reset documents stuck in INDEXING status to PENDING"""
    engine = get_db_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, filename, indexed_at
            FROM documents
            WHERE index_status = 'INDEXING'
        """)).fetchall()

    stuck_docs = []
    cutoff_time = datetime.now() - timedelta(minutes=10)

    for row in rows:
        doc_id, filename, indexed_at = row
        if indexed_at:
            try:
                indexed_time = datetime.fromisoformat(str(indexed_at))
                if indexed_time < cutoff_time:
                    stuck_docs.append((doc_id, filename, indexed_at))
            except:
                stuck_docs.append((doc_id, filename, indexed_at))

    if not stuck_docs:
        print("No stuck documents to reset")
        return 0

    if dry_run:
        print(f"\n[DRY RUN] Would reset {len(stuck_docs)} stuck documents to PENDING:")
        for doc_id, filename, indexed_at in stuck_docs[:10]:
            print(f"  - ID {doc_id}: {filename} (stuck since: {indexed_at})")
        if len(stuck_docs) > 10:
            print(f"  ... and {len(stuck_docs) - 10} more")
        return 0

    engine = get_db_engine()
    reset_count = 0
    with engine.connect() as conn:
        for doc_id, filename, indexed_at in stuck_docs:
            try:
                conn.execute(
                    text("""
                    UPDATE documents
                    SET index_status = 'PENDING', indexed_at = NULL, error_message = NULL
                    WHERE id = :id
                """),
                    {"id": doc_id},
                )
                reset_count += 1
            except Exception as e:
                print(f"Error resetting document {doc_id}: {e}")
        conn.commit()

    print(f"Reset {reset_count} stuck documents to PENDING status")
    return reset_count


def main():
    """Main cleanup function"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Clean up stale indexing jobs and orphaned documents"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be cleaned without actually doing it",
    )
    parser.add_argument(
        "--execute", action="store_true", help="Actually perform the cleanup"
    )
    args = parser.parse_args()

    dry_run = not args.execute

    if dry_run:
        print("=" * 80)
        print("DRY RUN MODE - No changes will be made")
        print("Run with --execute to actually perform cleanup")
        print("=" * 80)
    else:
        print("=" * 80)
        print("EXECUTING CLEANUP")
        print("=" * 80)

    print("\n1. Finding orphaned documents...")
    orphaned_docs = find_orphaned_documents()
    print(f"Found {len(orphaned_docs)} orphaned documents")

    print("\n2. Finding stale progress jobs...")
    stale_jobs = find_stale_progress_jobs()
    print(f"Found {len(stale_jobs)} stale job directories")

    print("\n3. Cleaning up orphaned documents...")
    cleanup_orphaned_documents(orphaned_docs, dry_run)

    print("\n4. Cleaning up stale jobs...")
    cleanup_stale_jobs(stale_jobs, dry_run)

    print("\n5. Resetting stuck documents...")
    reset_stuck_documents(dry_run)

    print("\n" + "=" * 80)
    if dry_run:
        print("Dry run complete. Run with --execute to perform cleanup.")
    else:
        print("Cleanup complete!")
    print("=" * 80)


if __name__ == "__main__":
    main()
