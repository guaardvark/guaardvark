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

# A catch-up a person started (Reset, a model switch, Bulk index, Resume
# pending): one at a time, one progress row, every named document tried once.
# Its state lives in Redis so a second click adds documents to the run in
# flight instead of starting a parallel one.
DRAIN_JOB_KEY = "guaardvark:index_drain:job"
DRAIN_IDS_KEY = "guaardvark:index_drain:ids"
DRAIN_TRIED_KEY = "guaardvark:index_drain:tried"
DRAIN_STATS_KEY = "guaardvark:index_drain:stats"
DRAIN_KEYS = (DRAIN_JOB_KEY, DRAIN_IDS_KEY, DRAIN_TRIED_KEY, DRAIN_STATS_KEY)
DRAIN_TTL_S = 900
# Next batch: queued behind whatever else is waiting, not held back.
DRAIN_NEXT_BATCH_S = 1
# While indexing cannot run (GPU or RAM pressure, embedder unreachable), the
# run checks again every DRAIN_WAIT_S, up to DRAIN_MAX_WAITS times, then hands
# what is left to the 15-minute catch-up.
DRAIN_WAIT_S = 30
DRAIN_MAX_WAITS = 10


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


def _redis():
    import redis
    url = os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=5)


def _drain_touch(r) -> None:
    for key in DRAIN_KEYS:
        r.expire(key, DRAIN_TTL_S)


def _drain_clear(r) -> None:
    r.delete(*DRAIN_KEYS)


def manual_drain_running() -> bool:
    """True while a catch-up a person started is in flight. Never raises."""
    try:
        return bool(_redis().get(DRAIN_JOB_KEY))
    except Exception:
        return False


def _report_index_job(job_id: str, message: str, progress: int, done: bool, failed: bool = False) -> None:
    """Update the one progress record of a catch-up a person started."""
    try:
        from backend.utils.unified_progress_system import get_unified_progress
        progress_system = get_unified_progress()
        if done and failed:
            progress_system.error_process(job_id, message)
        elif done:
            progress_system.complete_process(job_id, message)
        else:
            progress_system.update_process(job_id, int(progress), message)
    except Exception as e:
        logger.debug("index catch-up: progress update failed: %s", e)


def start_manual_drain(document_ids) -> tuple:
    """Index these documents in one catch-up. They must already be PENDING.

    Returns (job_id, error). When a catch-up is already running, the documents
    join it and its job id comes back; otherwise a progress row is created and
    one task queued. error is the exception that kept the task from starting.
    """
    ids = sorted({int(i) for i in document_ids or []})
    if not ids:
        return None, None
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType

    r = _redis()
    existing = r.get(DRAIN_JOB_KEY)
    if existing and _job_is_live(existing):
        r.sadd(DRAIN_IDS_KEY, *ids)
        r.srem(DRAIN_TRIED_KEY, *ids)
        _drain_touch(r)
        logger.info("index catch-up: %d document(s) joined the run in flight (%s)", len(ids), existing)
        return existing, None

    _drain_clear(r)
    progress_system = get_unified_progress()
    job_id = progress_system.create_process(
        ProcessType.INDEXING,
        f"Indexing {len(ids)} documents",
        {"target_count": len(ids), "generated_count": 0},
    )
    progress_system.update_process(job_id, 0, f"{len(ids)} documents waiting to be indexed",
                                   {"target_count": len(ids), "generated_count": 0})
    r.set(DRAIN_JOB_KEY, job_id, ex=DRAIN_TTL_S)
    r.sadd(DRAIN_IDS_KEY, *ids)
    _drain_touch(r)
    try:
        resume_pending_tick.apply_async(kwargs={"job_id": job_id, "manual": True}, queue="indexing")
    except Exception as exc:
        _drain_clear(r)
        _report_index_job(job_id, f"Indexing was not started: {exc}", 0, True, failed=True)
        return job_id, exc
    return job_id, None


def _schedule_drain(job_id: str, countdown: int) -> None:
    try:
        resume_pending_tick.apply_async(kwargs={"job_id": job_id, "manual": True},
                                        queue="indexing", countdown=countdown)
    except Exception as e:
        logger.warning("index catch-up: could not queue the next batch: %s", e)
        _report_index_job(job_id, f"Indexing stopped: the next batch could not be queued ({e})",
                          0, True, failed=True)
        try:
            _drain_clear(_redis())
        except Exception:
            pass


def _drain_summary(stats: dict, left: int) -> str:
    parts = []
    for key, label in (("indexed", "indexed"), ("stored", "stored without text or read in place"),
                       ("error", "failed"), ("unreadable", "compiled or binary"),
                       ("missing", "missing on disk")):
        n = int(stats.get(key) or 0)
        if n:
            parts.append(f"{n} {label}")
    text = ", ".join(parts) or "nothing to do"
    if left:
        text += f"; {left} left for the 15-minute catch-up"
    return f"Indexing finished: {text}"


def _manual_drain(job_id, isvc, db, DBDocument, limit) -> dict:
    """One batch of a catch-up a person started; queues the next one itself."""
    r = _redis()
    if r.get(DRAIN_JOB_KEY) != job_id:
        return {"skipped": "this catch-up is no longer the one in flight"}
    _drain_touch(r)

    paused = False
    try:
        paused = isvc.is_indexing_paused()
    except Exception:
        pass
    if paused:
        left = len(r.smembers(DRAIN_IDS_KEY))
        _report_index_job(job_id, f"Indexing is paused; {left} documents were left waiting.", 0, True)
        _drain_clear(r)
        return {"skipped": "indexing paused by operator"}

    wait_reason = None
    try:
        if isvc._under_resource_pressure():
            wait_reason = "the GPU or memory is busy"
    except Exception:
        pass
    if wait_reason is None and not _embedding_service_reachable():
        wait_reason = "the embedding model is not reachable"
    if wait_reason:
        waits = r.hincrby(DRAIN_STATS_KEY, "waits", 1)
        if waits > DRAIN_MAX_WAITS:
            stats = r.hgetall(DRAIN_STATS_KEY)
            left = len(r.smembers(DRAIN_IDS_KEY) - r.smembers(DRAIN_TRIED_KEY))
            _report_index_job(job_id, _drain_summary(stats, left) + f" ({wait_reason})", 100, True)
            _drain_clear(r)
            return {"skipped": f"gave up waiting: {wait_reason}"}
        _report_index_job(job_id, f"Waiting: {wait_reason}", 0, False)
        _schedule_drain(job_id, DRAIN_WAIT_S)
        return {"skipped": f"waiting: {wait_reason}"}
    r.hset(DRAIN_STATS_KEY, "waits", 0)

    ids = {int(i) for i in r.smembers(DRAIN_IDS_KEY)}
    tried = {int(i) for i in r.smembers(DRAIN_TRIED_KEY)}
    untried = sorted(ids - tried)
    batch = (DBDocument.query.filter(DBDocument.id.in_(untried), DBDocument.index_status == "PENDING")
             .order_by(DBDocument.id).limit(limit).all()) if untried else []
    if not batch:
        stats = r.hgetall(DRAIN_STATS_KEY)
        left = (DBDocument.query.filter(DBDocument.id.in_(sorted(ids)), DBDocument.index_status == "PENDING")
                .count()) if ids else 0
        _report_index_job(job_id, _drain_summary(stats, left), 100, True)
        _drain_clear(r)
        return {"done": True, "left": left}

    from backend.celery_tasks_isolated import index_one_document
    from backend.config import UPLOAD_DIR
    from backend.services import job_arbiter

    for doc in batch:
        job_arbiter.yield_point("indexing", doc.id, label=f"Indexing document {doc.id}")
        r.sadd(DRAIN_TRIED_KEY, doc.id)
        rel = getattr(doc, "file_path", None) or doc.path
        path = rel if os.path.isabs(rel) else os.path.join(UPLOAD_DIR, rel)
        if not os.path.exists(path):
            # The person asked for this document; say it is gone rather than
            # leave it waiting for a file that will not come back.
            doc.index_status = "ERROR"
            doc.error_message = f"File not found: {rel}"[:500]
            db.session.commit()
            r.hincrby(DRAIN_STATS_KEY, "missing", 1)
            continue
        outcome = index_one_document(doc.id)
        db.session.expire_all()
        status = outcome["status"]
        if status in ("error", "deferred") and not _embedding_service_reachable():
            # The service went away, the document is fine: back to waiting,
            # and this run waits with it.
            fresh = db.session.get(DBDocument, doc.id)
            if fresh is not None:
                fresh.index_status = "PENDING"
                fresh.error_message = None
                db.session.commit()
            r.srem(DRAIN_TRIED_KEY, doc.id)
            _schedule_drain(job_id, DRAIN_WAIT_S)
            return {"stopped": "embedding service went away mid-batch"}
        r.hincrby(DRAIN_STATS_KEY, status, 1)

    total = len(ids)
    done = len({int(i) for i in r.smembers(DRAIN_TRIED_KEY)} & ids)
    _report_index_job(job_id, f"{done} of {total} documents done", int(99 * done / max(1, total)), False)
    _schedule_drain(job_id, DRAIN_NEXT_BATCH_S)
    return {"batch": len(batch), "done": done, "total": total}


REAPED_INDEXING_NOTE = "Indexing did not finish; returned to the queue"


def release_reaped_indexing_job(metadata: dict):
    """Return the document a reaped indexing job left INDEXING to the queue.

    PENDING when this task can index its format, else NOT_INDEXED. A row now
    held by another job is left alone. Returns the new status, or None when
    nothing changed. Never raises.
    """
    if not isinstance(metadata, dict) or metadata.get("process_type") != "indexing":
        return None
    doc_id = (metadata.get("additional_data") or {}).get("document_id")
    if doc_id is None:
        return None
    try:
        from backend.models import db, Document as DBDocument
        doc = db.session.get(DBDocument, int(doc_id))
        if doc is None or doc.index_status != "INDEXING":
            return None
        job_id = metadata.get("job_id")
        if doc.indexing_job_id and job_id and doc.indexing_job_id != job_id:
            return None
        doc.index_status = "PENDING" if _is_indexable(doc, _document_extensions()) else "NOT_INDEXED"
        doc.indexing_job_id = None
        doc.error_message = REAPED_INDEXING_NOTE
        db.session.commit()
        logger.warning("index reap: document %s returned to %s", doc.id, doc.index_status)
        return doc.index_status
    except Exception as e:
        logger.error("index reap: could not return document %s: %s", doc_id, e)
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass
        return None


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
def resume_pending_tick(limit: int = None, job_id: str = None, manual: bool = False) -> dict:
    """Index a few PENDING documents, if conditions allow. Never raises.

    Skips entirely when: auto-resume is disabled, the operator's Pause Indexing
    toggle is on, or the machine is under GPU/RAM pressure. Each skip reports its
    reason — a catch-up job that silently does nothing is indistinguishable from
    one that has finished.

    With ``manual`` it is one batch of a catch-up a person started
    (start_manual_drain), which ignores GUAARDVARK_INDEX_AUTO_RESUME and queues
    its own next batch.
    """
    if not manual and not _enabled():
        return {"skipped": "disabled by GUAARDVARK_INDEX_AUTO_RESUME"}
    if not manual and manual_drain_running():
        return {"skipped": "a catch-up a person started is running"}

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

        if manual:
            try:
                return _manual_drain(job_id, isvc, db, DBDocument, limit)
            except Exception as e:
                logger.error("index catch-up %s failed: %s", job_id, e, exc_info=True)
                _report_index_job(job_id, f"Indexing stopped: {e}", 0, True, failed=True)
                try:
                    _drain_clear(_redis())
                except Exception:
                    pass
                return {"error": str(e)[:200]}

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
                from backend.celery_tasks_isolated import index_one_document
                outcome = index_one_document(doc.id)
                db.session.expire_all()
                status = outcome["status"]
                if status in ("indexed", "stored"):
                    indexed += 1
                elif status == "unreadable":
                    no_text += 1
                elif status == "deferred" or not _embedding_service_reachable():
                    # The vector store or the embedding service went away: the
                    # document is fine, so it goes back to waiting and the batch
                    # stops for the next tick.
                    fresh = db.session.get(DBDocument, doc.id)
                    if fresh is not None and fresh.index_status != "PENDING":
                        fresh.index_status = "PENDING"
                        fresh.error_message = None
                        db.session.commit()
                    logger.warning(
                        "index resume: embedding service went away mid-batch — "
                        "stopping, %d document(s) left PENDING", len(pending) - indexed,
                    )
                    return {"indexed": indexed, "failed": failed, "missing": missing,
                            "stopped": "embedding service went away mid-batch"}
                elif outcome.get("no_content"):
                    no_text += 1
                else:
                    # Not this task's to condemn: back to waiting, behind the rest.
                    fresh = db.session.get(DBDocument, doc.id)
                    if fresh is not None:
                        fresh.index_status = "PENDING"
                        _defer(fresh, db, outcome["message"])
                    failed += 1
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
