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

Two rules keep it from doing harm while it runs unattended:

*Status is not a scope.* The registry also holds generated media — batch image
and video output, recorded audio — registered as documents but never submitted
for text indexing. They outnumber real documents several to one and sort ahead of
them by id, so a job that walks "everything not yet INDEXED" in id order spends
its GPU captioning generated PNGs and never reaches the backlog it exists to
finish. Candidates are therefore restricted to formats this pipeline has a
document loader for.

*It does not condemn what it cannot index.* `add_file_to_index` returns falsy for
environmental reasons — the index not yet initialised, a model that would not
load — that say nothing about the document. Writing ERROR on that removes the row
from the set this task works from, so a bad half-hour becomes permanent. Failures
are recorded and the document is left where it is, for a human or an explicit
reindex to classify. The exception is a file with bytes in it from which no text
could be read (`no_content_reason`): that is about the file, so it ends ERROR
with the reason, as it does when indexed from the upload path.
"""

import logging
import os
from datetime import datetime, timedelta

from celery import shared_task
from backend.services.code_retrieval import STORED_ON_DEMAND

logger = logging.getLogger(__name__)

# A row left INDEXING with nothing indexing it never moves on its own: this
# task works from PENDING and STORED. Rows get there when a worker dies
# mid-document, and every row saved under the old model default (INDEXING)
# by a path that queued nothing is there too. reconcile_stuck_indexing runs
# at startup and resets only rows older than this. The bound is deliberately
# wide: Bulk Index marks rows INDEXING before queuing, without a job id, and
# a queued-but-not-started task is invisible to any worker probe, so age is
# the only safe signal.
STUCK_INDEXING_AFTER = timedelta(hours=6)
STUCK_INDEXING_NOTE = "Reset at startup: no indexing task was running this document"

# Small enough that a tick cannot monopolise the GPU, and frequent enough that an
# interrupted run finishes on its own within hours rather than never.
DEFAULT_BATCH = int(os.environ.get("GUAARDVARK_INDEX_RESUME_BATCH", "5"))


# Connection-shaped failures mean "the service is down", not "this document is
# bad". Stopping the batch on one keeps the remaining documents untouched for the
# next tick instead of walking the whole batch against a service that is gone.
_TRANSIENT = ("connect", "connection", "timeout", "refused", "temporarily unavailable",
              "broken pipe", "reset by peer")

# Plain-text formats that need no dedicated loader. Structured formats come from
# the loaders themselves below, so this list does not drift as they gain support.
_PLAIN_TEXT = {".txt", ".text", ".rst", ".log", ".csv", ".json", ".yaml", ".yml"}


def _is_transient(exc: Exception) -> bool:
    return any(t in str(exc).lower() for t in _TRANSIENT)


def _enabled() -> bool:
    return os.environ.get("GUAARDVARK_INDEX_AUTO_RESUME", "true").lower() == "true"


def _document_extensions() -> set:
    """Formats this task will service, composed from the loaders that handle them.

    Deriving the set from the loaders keeps it in step with them automatically
    rather than adding one more hardcoded extension list to the several the
    ingest path already carries. Override with a comma-separated
    GUAARDVARK_INDEX_RESUME_EXTENSIONS to widen or narrow it per machine.
    """
    env = os.environ.get("GUAARDVARK_INDEX_RESUME_EXTENSIONS", "").strip()
    if env:
        parts = (p.strip().lower() for p in env.split(","))
        return {p if p.startswith(".") else "." + p for p in parts if p}

    exts = set(_PLAIN_TEXT)
    try:
        from backend.utils.docling_loader import SUPPORTED_EXTENSIONS as docling_exts
        exts |= set(docling_exts)
    except Exception:
        exts |= {".pdf", ".docx", ".pptx"}
    try:
        from backend.utils.markdown_sections import SUPPORTED_EXTENSIONS as md_exts
        exts |= set(md_exts)
    except Exception:
        exts |= {".md", ".markdown", ".mdx"}
    return {e.lower() for e in exts}


def _embedding_service_reachable() -> bool:
    """Cheap preflight against Ollama's API.

    Without this the task walks its batch calling an unreachable service once per
    document, logging a stack trace each time and achieving nothing. One check up
    front turns that into a single, quiet "come back later".
    """
    try:
        import requests
        from backend.config import OLLAMA_BASE_URL
        return requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3).ok
    except Exception:
        return False


def _defer(doc, db, note: str) -> None:
    """Record an attempt without reclassifying the document.

    Touching the row bumps `updated_at`, which is the ordering key, so a document
    that cannot be indexed moves to the back of the queue instead of being
    retried every tick — the starvation the ordering exists to prevent, reached
    without having to declare the document broken.
    """
    doc.error_message = note[:500]
    doc.updated_at = datetime.now()
    db.session.commit()


def _job_is_live(job_id) -> bool:
    """Whether the unified progress system still tracks this indexing job as
    running. Finished jobs drop out of it about a minute after they end."""
    if not job_id:
        return False
    try:
        from backend.utils.unified_progress_system import get_unified_progress
        info = get_unified_progress().get_job_status(job_id)
    except Exception:
        return False
    if not info:
        return False
    return not info.get("is_complete") and info.get("status") not in ("complete", "error", "cancelled")


def _is_indexable(doc, exts: set) -> bool:
    candidates = {(doc.type or "").lower(), os.path.splitext(doc.path or doc.filename or "")[1].lower()}
    return bool(candidates & exts)


def reconcile_stuck_indexing() -> dict:
    """Move INDEXING rows that nothing is indexing to where they belong.

    Only rows older than STUCK_INDEXING_AFTER (by updated_at, else
    uploaded_at) are touched, and a row whose indexing_job_id the progress
    system still tracks is left alone. A format this task has a loader for
    becomes PENDING, so resume_pending_tick picks it up; anything else
    (generated media registered under the old default) becomes NOT_INDEXED,
    since PENDING would promise an indexing that never comes. Returns the
    counts. Never raises.
    """
    try:
        from backend.models import db, Document as DBDocument
        cutoff = datetime.now() - STUCK_INDEXING_AFTER
        rows = DBDocument.query.filter(DBDocument.index_status == "INDEXING").all()
        if not rows:
            return {"pending": 0, "not_indexed": 0}
        exts = _document_extensions()
        pending = not_indexed = 0
        for doc in rows:
            stamp = doc.updated_at or doc.uploaded_at
            if not (stamp and stamp < cutoff):
                continue
            if _job_is_live(doc.indexing_job_id):
                continue
            if _is_indexable(doc, exts):
                doc.index_status = "PENDING"
                pending += 1
            else:
                doc.index_status = "NOT_INDEXED"
                not_indexed += 1
            doc.error_message = STUCK_INDEXING_NOTE
        if pending or not_indexed:
            db.session.commit()
            logger.warning("index reconcile: %d stuck INDEXING row(s) reset: %d to PENDING, %d to NOT_INDEXED",
                           pending + not_indexed, pending, not_indexed)
        return {"pending": pending, "not_indexed": not_indexed}
    except Exception as e:
        logger.error("index reconcile: could not reset stuck INDEXING rows: %s", e)
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass
        return {"pending": 0, "not_indexed": 0, "error": str(e)[:200]}


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
            from sqlalchemy import or_, func
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
                logger.info("index resume: deferring — resource pressure")
                return {"skipped": "resource pressure — deferring"}
        except Exception:
            pass

        if not _embedding_service_reachable():
            logger.info("index resume: deferring — embedding service unreachable")
            return {"skipped": "embedding service unreachable — deferring"}

        exts = _document_extensions()
        try:
            # `type` carries the extension for almost every row; fall back to the
            # path so the few rows without one are still reachable.
            match = [func.lower(DBDocument.type).in_(sorted(exts))]
            match += [DBDocument.path.ilike("%" + e) for e in sorted(exts)]
            pending = (
                DBDocument.query
                .filter(DBDocument.index_status.in_(["PENDING", "STORED"]))
                .filter(or_(*match))
                # Least-recently-attempted first, so a document that keeps failing
                # cannot hold the front of the queue against the rest of the backlog.
                .order_by(DBDocument.updated_at.asc().nullsfirst(), DBDocument.id)
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

        indexed = failed = missing = no_text = 0
        from backend.services import job_arbiter
        for doc in pending:
            # Job order on: hold between documents while a chat or voice turn runs.
            job_arbiter.yield_point("indexing", doc.id, label=f"Indexing document {doc.id}")
            rel = getattr(doc, "file_path", None) or doc.path
            path = rel if os.path.isabs(rel) else os.path.join(UPLOAD_DIR, rel)
            if not os.path.exists(path):
                # Record it and move on. A registry row pointing at a file that is
                # gone is a reconciliation question, not this task's to answer:
                # rewriting its status would silently rewrite history the operator
                # may still want to see.
                _defer(doc, db, f"file not found: {path}")
                missing += 1
                continue
            try:
                ok = isvc.add_file_to_index(path, doc)
                if ok == STORED_ON_DEMAND:
                    indexed += 1  # code: the row is STORED, read in place
                elif ok:
                    doc.index_status = "INDEXED"
                    doc.indexed_at = datetime.now()
                    doc.error_message = None
                    indexed += 1
                else:
                    reason = isvc.no_content_reason(doc.id)
                    if reason:
                        doc.index_status = "ERROR"
                        doc.error_message = reason[:500]
                        doc.updated_at = datetime.now()
                        db.session.commit()
                        no_text += 1
                        continue
                    # A falsy return here is usually the embedding call failing
                    # inside add_file_to_index. Re-check the service rather than
                    # condemning the document: if it went away mid-batch, stop and
                    # leave the rest PENDING for the next tick.
                    if not _embedding_service_reachable():
                        logger.warning(
                            "index resume: embedding service went away mid-batch — "
                            "stopping, %d document(s) left PENDING", len(pending) - indexed,
                        )
                        db.session.rollback()
                        return {"indexed": indexed, "failed": failed, "missing": missing,
                                "stopped": "embedding service went away mid-batch"}
                    _defer(doc, db, "add_file_to_index returned falsy")
                    failed += 1
                    continue
            except Exception as exc:
                if _is_transient(exc):
                    # Leave it PENDING. The service is down, the document is fine.
                    logger.info("index resume: transient failure on %s — leaving PENDING (%s)",
                                doc.filename, str(exc)[:100])
                    db.session.rollback()
                    return {"indexed": indexed, "failed": failed, "missing": missing,
                            "stopped": f"transient: {str(exc)[:120]}"}
                _defer(doc, db, str(exc)[:500])
                failed += 1
                logger.warning("index resume: %s failed: %s", doc.filename, str(exc)[:160])
                continue
            db.session.commit()

        # Application logging defaults to WARNING (BACKEND_LOG_LEVEL), so an INFO
        # summary is invisible on a normal install — and an unattended job whose
        # outcome nobody can see is one nobody can trust. A tick that deferred or
        # skipped documents is saying something needs attention, so it reports at
        # WARNING; a clean tick stays quiet.
        summary = "index resume tick: %d indexed, %d deferred, %d missing, %d with no text (ERROR)"
        if failed or missing or no_text:
            logger.warning(summary, indexed, failed, missing, no_text)
        else:
            logger.info(summary, indexed, failed, missing, no_text)
        return {"indexed": indexed, "failed": failed, "missing": missing, "no_text": no_text}
