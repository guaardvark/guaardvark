#!/usr/bin/env python3
"""Index documents sitting in PENDING, using the in-process pipeline.

The live backend loads its modules at boot, so a long-running instance keeps the
ingest code it started with. This driver runs the current code directly, which
matters when the pipeline itself has just changed.

Documents are read from the Document registry -- the source of truth -- so this
indexes exactly what was imported, and marks each row's status honestly whether
it succeeds or fails.
"""

import argparse
import logging
import sys
import time
import warnings

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.ERROR)
log = logging.getLogger("index_pending")
log.setLevel(logging.INFO)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="Stop after N documents (0 = all)")
    ap.add_argument("--path-prefix", default=None,
                    help="REQUIRED unless --all: only touch documents whose path starts with this. "
                         "Status alone is not a scope -- an unscoped run walks the entire corpus "
                         "oldest-first and reclassifies rows that were never the target.")
    ap.add_argument("--all", action="store_true",
                    help="Explicitly opt in to every matching document, ignoring --path-prefix.")
    ap.add_argument("--status", default="PENDING",
                    help="Comma-separated index_status values to pick up. "
                         "Note bulk import leaves binary documents (docx/pdf) as STORED "
                         "and never queues them, so they need naming explicitly.")
    args = ap.parse_args()

    from flask import Flask
    from backend.models import db, Document as DBDocument
    from backend.config import DATABASE_URL

    app = Flask(__name__)
    app.config.update({
        "SQLALCHEMY_DATABASE_URI": DATABASE_URL,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
    })
    db.init_app(app)

    with app.app_context():
        import backend.services.indexing_service as isvc
        isvc.get_or_create_index()
        if isvc.index is None:
            log.error("index unavailable — aborting")
            return 1

        statuses = [s.strip() for s in args.status.split(",") if s.strip()]
        if not args.path_prefix and not args.all:
            log.error("refusing to run unscoped: pass --path-prefix, or --all to mean it")
            return 2
        q = DBDocument.query.filter(DBDocument.index_status.in_(statuses))
        if args.path_prefix:
            q = q.filter(DBDocument.path.like(f"{args.path_prefix}%"))
        q = q.order_by(DBDocument.id)
        if args.limit:
            q = q.limit(args.limit)
        docs = q.all()
        log.info("%d document(s) with status in %s, scope=%s",
                 len(docs), statuses, args.path_prefix or "ALL")

        import os
        from backend.config import UPLOAD_DIR

        ok = failed = skipped = 0
        t0 = time.time()
        for i, doc in enumerate(docs, 1):
            try:
                # Document.path is stored relative to the uploads root, the same way
                # the indexing handler resolves it. Passing it through unresolved
                # silently "fails" every document with a not-found.
                rel = getattr(doc, "file_path", None) or doc.path
                file_path = rel if os.path.isabs(rel) else os.path.join(UPLOAD_DIR, rel)
                if not os.path.exists(file_path):
                    doc.index_status = "ERROR"
                    doc.error_message = f"file not found: {file_path}"
                    skipped += 1
                    db.session.commit()
                    continue
                result = isvc.add_file_to_index(file_path, doc)
                if result:
                    doc.index_status = "INDEXED"
                    from datetime import datetime
                    doc.indexed_at = datetime.now()
                    ok += 1
                else:
                    doc.index_status = "ERROR"
                    doc.error_message = "add_file_to_index returned falsy"
                    failed += 1
            except Exception as exc:
                doc.index_status = "ERROR"
                doc.error_message = str(exc)[:500]
                failed += 1
                log.warning("failed %s: %s", doc.filename, str(exc)[:160])
            db.session.commit()

            if i % 10 == 0 or i == len(docs):
                rate = i / max(1e-6, time.time() - t0)
                remaining = (len(docs) - i) / rate if rate else 0
                log.info("%d/%d  ok=%d failed=%d  %.2f docs/s  ~%.0f min left",
                         i, len(docs), ok, failed, rate, remaining / 60)

        log.info("done: %d indexed, %d failed, %d missing, %.0f s", ok, failed, skipped, time.time() - t0)
        try:
            log.info("vector store: %s", isvc.vector_store_stats())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
