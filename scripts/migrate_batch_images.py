#!/usr/bin/env python3
"""
Migrate existing batch images into the Documents/Files system.
Scans data/outputs/batch_images/ for completed batches and registers them.

Usage:
    python3 scripts/migrate_batch_images.py [--dry-run]
"""

import json
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
os.environ.setdefault("GUAARDVARK_ROOT", str(project_root))


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate batch images to Documents system"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without doing it",
    )
    args = parser.parse_args()

    from backend.app import create_app

    app = create_app()

    with app.app_context():
        from backend.services.image_registration_service import register_batch_images

        batch_base = project_root / "data" / "outputs" / "batch_images"
        if not batch_base.exists():
            print("No batch images directory found. Nothing to migrate.")
            return

        batch_dirs = sorted(
            [
                d
                for d in batch_base.iterdir()
                if d.is_dir() and not d.name.startswith("_")
            ]
        )

        print(f"Found {len(batch_dirs)} batch directories")

        migrated = 0
        skipped = 0
        errors = 0

        for batch_dir in batch_dirs:
            batch_id = batch_dir.name
            images_dir = batch_dir / "images"

            if not images_dir.exists() or not any(images_dir.iterdir()):
                print(f"  SKIP {batch_id}: no images")
                skipped += 1
                continue

            metadata_file = batch_dir / "batch_metadata.json"
            batch_name = batch_id
            if metadata_file.exists():
                try:
                    meta = json.loads(metadata_file.read_text())
                    batch_name = meta.get("batch_name", batch_id)
                except Exception:
                    pass

            image_count = len(list(images_dir.glob("*")))

            if args.dry_run:
                print(
                    f"  WOULD MIGRATE {batch_id} ({image_count} images) -> /Images/{batch_name}/"
                )
                migrated += 1
                continue

            try:
                folder, docs = register_batch_images(
                    batch_id=batch_id,
                    batch_output_dir=str(batch_dir),
                    batch_name=batch_name,
                )
                print(f"  MIGRATED {batch_id} -> {folder.path} ({len(docs)} images)")
                migrated += 1
            except Exception as e:
                print(f"  ERROR {batch_id}: {e}")
                errors += 1

        print(f"\nDone: {migrated} migrated, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
