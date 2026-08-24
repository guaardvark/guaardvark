"""Periodic requeue of documents left PENDING.

Indexing state lives in the `Document` registry, so an interrupted run — a
reboot, a killed worker, a machine that went to sleep — loses only the running
process, never the progress. Nothing restarted it, though: documents sat PENDING
indefinitely until somebody noticed and ran the indexer by hand. On a corpus of
several hundred files that is easy to miss, because a half-indexed knowledge base
answers questions, just not from everything.

This closes that gap. It is deliberately unhurried and yields to everything else:
embedding competes with image and video generation for the same GPU, and a
background catch-up job has no business winning that contest.
"""

import logging
import os

from celery import shared_task

logger = logging.getLogger(__name__)

# Small enough that a tick cannot monopolise the GPU, and frequent enough that an
# interrupted run finishes on its own within hours rather than never.
DEFAULT_BATCH = int(os.environ.get("GUAARDVARK_INDEX_RESUME_BATCH", "5"))


def _enabled() -> bool:
    return os.environ.get("GUAARDVARK_INDEX_AUTO_RESUME", "true").lower() == "true"


@shared_task(name="indexing.resume_pending_tick")
def resume_pending_tick(limit: int = None) -> dict:
    """Index a few PENDING documents, if conditions allow. Never raises.

    Skips entirely when: auto-resume is disabled, the operator's Pause Indexing
    toggle is on, or the machine is under GPU/RAM pressure. Each skip reports its
    reason — a catch-up job that silently does nothing is indistinguishable from
    one that has finished.
    """
    if not _enabled():
        return {"skipped": "disabled by GUAARDVARK_INDEX_AUTO_RESUME"}

    limit = int(limit or DEFAULT_BATCH)
    try:
        from backend.app import get_or_create_app
        app = get_or_create_app()
    except Exception as e:
        logger.debug("index resume: no app context (%s)", e)
        return {"skipped": f"no app context: {e}"}

    with app.app_context():
        try:
            import backend.services.indexing_service as isvc
            from backend.models import db, Document as DBDocument
        except Exception as e:
            return {"skipped": f"imports unavailable: {e}"}

        try:
            if isvc.is_indexing_paused():
                return {"skipped": "indexing paused by operator"}
        except Exception:
            pass

        # Yield the GPU. Retrieval already degrades under this condition; a
        # catch-up job should simply wait for a quieter moment.
        try:
            if isvc._under_resource_pressure():
                return {"skipped": "resource pressure — deferring"}
        except Exception:
            pass

        try:
            pending = (
                DBDocument.query
                .filter(DBDocument.index_status.in_(["PENDING", "STORED"]))
                .order_by(DBDocument.id)
                .limit(limit)
                .all()
            )
        except Exception as e:
            logger.warning("index resume: could not query pending documents: %s", e)
            return {"error": str(e)[:200]}

        if not pending:
            return {"pending": 0}

        from backend.config import UPLOAD_DIR
        from datetime import datetime

        indexed = failed = missing = 0
        for doc in pending:
            rel = getattr(doc, "file_path", None) or doc.path
            path = rel if os.path.isabs(rel) else os.path.join(UPLOAD_DIR, rel)
            if not os.path.exists(path):
                # Mark rather than retry forever: a file that is gone will never
                # index, and leaving it PENDING makes every tick redo the lookup.
                doc.index_status = "ERROR"
                doc.error_message = f"file not found: {path}"
                missing += 1
                db.session.commit()
                continue
            try:
                ok = isvc.add_file_to_index(path, doc)
                doc.index_status = "INDEXED" if ok else "ERROR"
                if ok:
                    doc.indexed_at = datetime.now()
                    indexed += 1
                else:
                    doc.error_message = "add_file_to_index returned falsy"
                    failed += 1
            except Exception as exc:
                doc.index_status = "ERROR"
                doc.error_message = str(exc)[:500]
                failed += 1
                logger.warning("index resume: %s failed: %s", doc.filename, str(exc)[:160])
            db.session.commit()

        logger.info("index resume tick: %d indexed, %d failed, %d missing",
                    indexed, failed, missing)
        return {"indexed": indexed, "failed": failed, "missing": missing}
