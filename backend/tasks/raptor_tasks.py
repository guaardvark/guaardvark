"""Build corpus summaries in the background.

RAPTOR is one LLM call per cluster per level over the whole corpus, so it is
minutes of sustained GPU work -- far past what an HTTP request should hold open,
and directly in contention with image and video generation. It runs as a task so
the caller gets an id back immediately and the work goes through the same queue
as every other long GPU job.

There is no schedule. Summaries describe a corpus at a moment, and rebuilding
them is a decision about when to spend the GPU, not something to do on a timer
behind the operator's back.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="raptor.build_tree", bind=True)
def build_raptor_tree_task(self, project_id=None, max_levels: int = 2, replace: bool = True):
    """Run a RAPTOR build. Never raises: a failure is a result, not a crashed worker."""
    try:
        from backend.app import get_or_create_app
        app = get_or_create_app()
    except Exception as e:
        logger.error("RAPTOR build: no app context (%s)", e)
        return {"ok": False, "error": f"no app context: {e}"}

    with app.app_context():
        try:
            import backend.services.indexing_service as isvc
            if isvc.is_indexing_paused():
                return {"ok": False, "skipped": "indexing paused by operator"}
        except Exception:
            pass

        try:
            from backend.services.raptor_service import build_raptor_tree
            result = build_raptor_tree(
                project_id=project_id, max_levels=max_levels, replace=replace
            )
            logger.info("RAPTOR build finished: %s", result)
            return {"ok": True, **(result or {})}
        except Exception as e:
            logger.error("RAPTOR build failed: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)[:300]}
