#!/usr/bin/env python3

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger("bulk_import_docs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bulk import documents into Guaardvark file manager and RAG index."
    )
    parser.add_argument(
        "--source",
        "-s",
        required=True,
        help="Source directory containing files to import.",
    )
    parser.add_argument(
        "--target",
        "-t",
        default="Imports",
        help=(
            "Target top-level folder path inside the file manager "
            "(relative, e.g. 'Imports/ClientA'). Ignored when importing "
            "in-place from the existing uploads directory."
        ),
    )
    parser.add_argument(
        "--project-id",
        type=int,
        default=None,
        help="Optional project_id to associate with all imported documents.",
    )
    parser.add_argument(
        "--client-id",
        type=int,
        default=None,
        help="Optional client_id to associate with all imported documents.",
    )
    parser.add_argument(
        "--website-id",
        type=int,
        default=None,
        help="Optional website_id to associate with all imported documents.",
    )
    parser.add_argument(
        "--reindex-missing",
        action="store_true",
        help="If a Document already exists but is not INDEXED, re-run indexing.",
    )
    parser.add_argument(
        "--force-copy",
        action="store_true",
        help=(
            "Always copy files under uploads/target, even if source is already "
            "inside the uploads directory."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report what would be done, without copying or writing to DB.",
    )
    return parser.parse_args()


def normalize_rel_path(path: str) -> str:
    if not path:
        return ""
    cleaned = path.strip().strip("/").replace("\\", "/")
    if cleaned in (".", "./"):
        return ""
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    return cleaned


def init_logging():
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def ensure_folder(
    db, FolderModel, path_rel: str, cache: Dict[str, "FolderModel"]
) -> Optional["FolderModel"]:
    path_rel = normalize_rel_path(path_rel)
    if not path_rel:
        return None

    if path_rel in cache:
        return cache[path_rel]

    existing = FolderModel.query.filter_by(path=path_rel).first()
    if existing:
        cache[path_rel] = existing
        return existing

    parent_path = ""
    if "/" in path_rel:
        parent_path = path_rel.rsplit("/", 1)[0]
    parent_folder = ensure_folder(db, FolderModel, parent_path, cache) if parent_path else None

    name = path_rel.split("/")[-1]
    folder = FolderModel(name=name, path=path_rel, parent_id=parent_folder.id if parent_folder else None)
    db.session.add(folder)
    db.session.commit()
    cache[path_rel] = folder
    logger.info("Created folder '%s' (path=%s)", name, path_rel)
    return folder


def determine_mode(
    uploads_dir: Path, source_dir: Path, force_copy: bool
) -> Tuple[bool, Optional[Path]]:
    try:
        uploads_dir_resolved = uploads_dir.resolve()
        source_dir_resolved = source_dir.resolve()
    except FileNotFoundError:
        uploads_dir_resolved = uploads_dir
        source_dir_resolved = source_dir

    source_under_uploads = (
        uploads_dir_resolved == source_dir_resolved
        or uploads_dir_resolved in source_dir_resolved.parents
    )

    if force_copy:
        return True, None

    if source_under_uploads:
        rel = source_dir_resolved.relative_to(uploads_dir_resolved)
        return False, rel

    return True, None


def main():
    init_logging()
    args = parse_args()

    from backend.app import app
    from backend.models import db, Folder as FolderModel, Document as DocumentModel
    from backend.services.indexing_service import add_file_to_index, update_document_status

    source_dir = Path(args.source).expanduser().resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        logger.error("Source directory does not exist or is not a directory: %s", source_dir)
        sys.exit(1)

    with app.app_context():
        uploads_dir = Path(app.config["UPLOAD_FOLDER"]).expanduser().resolve()
        logger.info("Uploads directory: %s", uploads_dir)
        logger.info("Source directory : %s", source_dir)

        copy_mode, in_place_base_rel = determine_mode(uploads_dir, source_dir, args.force_copy)
        if copy_mode:
            logger.info("Mode: COPY into uploads/ under target '%s'", args.target)
        else:
            logger.info(
                "Mode: IN-PLACE import from uploads/ (base relative path: %s)",
                in_place_base_rel.as_posix() if in_place_base_rel else "/",
            )
            if args.target and args.target != "Imports":
                logger.info("Note: --target is ignored in in-place mode.")

        target_root_rel = normalize_rel_path(args.target)

        all_files = []
        for root, _dirs, files in os.walk(source_dir):
            for fname in files:
                all_files.append(Path(root) / fname)

        if not all_files:
            logger.warning("No files found under %s", source_dir)
            sys.exit(0)

        logger.info("Found %d files to consider for import.", len(all_files))

        if args.dry_run:
            logger.info("Dry run enabled. No filesystem or database changes will be made.")

        folder_cache: Dict[str, FolderModel] = {}
        processed_docs = 0
        indexed_docs = 0
        skipped_existing = 0
        reindexed = 0

        for idx, file_path in enumerate(all_files, start=1):
            if copy_mode:
                rel_from_source = file_path.parent.relative_to(source_dir)
                rel_from_source_str = "" if str(rel_from_source) == "." else rel_from_source.as_posix()

                if target_root_rel:
                    folder_rel = normalize_rel_path(
                        f"{target_root_rel}/{rel_from_source_str}" if rel_from_source_str else target_root_rel
                    )
                else:
                    folder_rel = normalize_rel_path(rel_from_source_str)

                dest_root = uploads_dir / (folder_rel if folder_rel else "")
                dest_root.mkdir(parents=True, exist_ok=True)
                dest_file_path = dest_root / file_path.name
                doc_path_rel = normalize_rel_path(
                    f"{folder_rel}/{file_path.name}" if folder_rel else file_path.name
                )
            else:
                assert in_place_base_rel is not None
                rel_in_source = file_path.parent.relative_to(source_dir)
                rel_in_uploads = (in_place_base_rel / rel_in_source).as_posix()
                folder_rel = normalize_rel_path(rel_in_uploads)
                dest_file_path = file_path
                doc_path_rel = normalize_rel_path(
                    f"{rel_in_uploads}/{file_path.name}" if rel_in_uploads else file_path.name
                )

            logger.info(
                "[%d/%d] Processing %s -> path='%s'",
                idx,
                len(all_files),
                file_path,
                doc_path_rel,
            )

            if args.dry_run:
                continue

            if copy_mode:
                if not dest_file_path.exists():
                    shutil.copy2(file_path, dest_file_path)
                    logger.info("Copied to %s", dest_file_path)
                else:
                    logger.info("Destination file already exists, reusing: %s", dest_file_path)

            folder_obj = ensure_folder(db, FolderModel, folder_rel, folder_cache)

            existing_doc: Optional[DocumentModel] = (
                DocumentModel.query.filter_by(path=doc_path_rel).first()
            )

            if existing_doc:
                processed_docs += 1
                logger.info(
                    "Document already exists in DB (id=%s, status=%s)",
                    existing_doc.id,
                    existing_doc.index_status,
                )
                if args.reindex_missing and existing_doc.index_status != "INDEXED":
                    logger.info(
                        "Re-indexing existing document id=%s with status=%s",
                        existing_doc.id,
                        existing_doc.index_status,
                    )
                    try:
                        update_document_status(existing_doc.id, "INDEXING")
                        success = add_file_to_index(str(dest_file_path), existing_doc, progress_callback=None)
                        if success:
                            update_document_status(existing_doc.id, "INDEXED")
                            indexed_docs += 1
                            reindexed += 1
                        else:
                            update_document_status(
                                existing_doc.id,
                                "ERROR",
                                "Bulk import reindex failed",
                            )
                    except Exception as e:
                        logger.exception("Reindexing failed for document %s: %s", existing_doc.id, e)
                        update_document_status(
                            existing_doc.id,
                            "ERROR",
                            f"Bulk import reindex exception: {e}",
                        )
                else:
                    skipped_existing += 1
                continue

            file_size = dest_file_path.stat().st_size if dest_file_path.exists() else None
            doc = DocumentModel(
                filename=file_path.name,
                path=doc_path_rel,
                type=dest_file_path.suffix.lower(),
                index_status="PENDING",
                size=file_size,
                folder_id=folder_obj.id if folder_obj else None,
                project_id=args.project_id,
                client_id=args.client_id,
                website_id=args.website_id,
            )
            db.session.add(doc)
            db.session.commit()
            processed_docs += 1
            logger.info("Created Document id=%s for %s", doc.id, doc_path_rel)

            try:
                update_document_status(doc.id, "INDEXING")
                success = add_file_to_index(str(dest_file_path), doc, progress_callback=None)
                if success:
                    update_document_status(doc.id, "INDEXED")
                    indexed_docs += 1
                else:
                    update_document_status(
                        doc.id,
                        "ERROR",
                        "Bulk import indexing failed",
                    )
            except Exception as e:
                logger.exception("Indexing failed for document %s: %s", doc.id, e)
                update_document_status(
                    doc.id,
                    "ERROR",
                    f"Bulk import indexing exception: {e}",
                )

        logger.info("Bulk import finished.")
        logger.info("  Documents processed : %d", processed_docs)
        logger.info("  Newly indexed       : %d", indexed_docs)
        logger.info("  Re-indexed existing : %d", reindexed)
        logger.info("  Skipped existing    : %d", skipped_existing)


if __name__ == "__main__":
    main()

