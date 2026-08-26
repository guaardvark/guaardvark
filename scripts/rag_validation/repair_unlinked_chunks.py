#!/usr/bin/env python3
"""Re-index documents whose chunks lost the link back to them.

A chunk's `document_id` comes from its SOURCE relationship. Several chunker paths
built nodes from raw text with no parent to inherit it from, so the vector store
recorded the literal string "None". Those rows cannot be purged, cannot be
replaced on re-index, and are not removed when their document is deleted -- they
accumulate silently, and a re-index of the same document adds a second copy
beside them rather than replacing the first.

The chunkers now set the relationship centrally, so new ingests are correct. Rows
already written are repaired by re-indexing their document: the id and its content
hash are derived at ingest, so regenerating them is more honest than inventing an
id that never corresponded to anything.

    scripts/rag_validation/repair_unlinked_chunks.py --dry-run
    scripts/rag_validation/repair_unlinked_chunks.py
"""

import argparse
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")


def say(msg=""):
    # Root logging is reset to WARNING once backend.app is imported.
    print(msg, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Report, change nothing")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N documents (0 = all)")
    args = ap.parse_args()

    from flask import Flask
    from backend.models import db, Document as DBDocument
    from backend.config import DATABASE_URL, UPLOAD_DIR

    app = Flask(__name__)
    app.config.update({"SQLALCHEMY_DATABASE_URI": DATABASE_URL,
                       "SQLALCHEMY_TRACK_MODIFICATIONS": False})
    db.init_app(app)

    with app.app_context():
        import backend.services.indexing_service as isvc
        isvc.get_or_create_index()
        if isvc.index is None:
            say("index unavailable - aborting")
            return 1
        table = "data_" + (isvc.resolve_existing_vector_table(None) or "")

        conn = isvc._pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT metadata_->>'source_filename' AS src, count(*) "
                    f'FROM "{table}" WHERE metadata_->>\'document_id\' = %s '
                    f"GROUP BY 1 ORDER BY 2 DESC", ("None",))
                affected = cur.fetchall()
        finally:
            conn.close()

        if not affected:
            say("No unlinked chunks found.")
            return 0

        total_rows = sum(n for _, n in affected)
        say(f"{len(affected)} document(s), {total_rows} unlinked chunk(s)")
        if args.dry_run:
            for src, n in affected[:15]:
                say(f"  {n:>5}  {src}")
            if len(affected) > 15:
                say(f"  ... and {len(affected) - 15} more")
            return 0

        todo = affected[: args.limit] if args.limit else affected
        repaired = skipped = failed = 0
        t0 = time.time()
        for i, (src, n) in enumerate(todo, 1):
            doc = DBDocument.query.filter(DBDocument.filename == src).first()
            if doc is None:
                say(f"  [{i}/{len(todo)}] no registry row for {src!r} - skipping")
                skipped += 1
                continue
            rel = getattr(doc, "file_path", None) or doc.path
            path = rel if os.path.isabs(rel) else os.path.join(UPLOAD_DIR, rel)
            if not os.path.exists(path):
                say(f"  [{i}/{len(todo)}] file missing for {src!r} - skipping")
                skipped += 1
                continue

            # Remove the unlinked rows first: they carry no document id, so the
            # ingest path's own purge cannot see them and would leave them behind
            # as duplicates of what it is about to write.
            conn = isvc._pg_connect()
            try:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(
                        f'DELETE FROM "{table}" WHERE metadata_->>\'document_id\' = %s '
                        f"AND metadata_->>'source_filename' = %s", ("None", src))
                    dropped = cur.rowcount or 0
            finally:
                conn.close()

            try:
                ok = isvc.add_file_to_index(path, doc)
                if ok:
                    from datetime import datetime
                    doc.index_status = "INDEXED"
                    doc.indexed_at = datetime.now()
                    doc.error_message = None
                    repaired += 1
                else:
                    doc.error_message = "repair: add_file_to_index returned falsy"
                    failed += 1
            except Exception as exc:
                doc.error_message = str(exc)[:500]
                failed += 1
                say(f"  [{i}/{len(todo)}] {src!r} FAILED: {str(exc)[:110]}")
            db.session.commit()
            say(f"  [{i}/{len(todo)}] {src[:52]:<52} dropped={dropped:>4} "
                f"ok={repaired} failed={failed} ({(time.time()-t0)/i:.1f}s/doc)")

        say(f"\ndone: {repaired} repaired, {failed} failed, {skipped} skipped, "
            f"{time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
