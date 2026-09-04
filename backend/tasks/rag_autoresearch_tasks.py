"""Celery tasks for RAG Autoresearch — idle detection, scheduled runs, event triggers."""
import logging

logger = logging.getLogger(__name__)


def create_autoresearch_tasks(celery_app):
    """Create autoresearch Celery tasks."""

    @celery_app.task(name="autoresearch.check_idle")
    def check_idle_and_start():
        """Runs every 10 min. Starts tonight's research run inside the
        configured nightly window, if auto mode is opted in.

        Replaces the old idle-detection start: in this process is_idle() was
        ALWAYS true after 10 minutes of worker uptime (activity tracking lives
        in the web process), which — combined with an unbounded run_loop and
        an empty eval set — self-started the 2026-08 134M-row runaway. A
        window-scheduled BOUNDED run has no such failure mode: preconditions
        fail loudly, the wall clock caps it, and one run per night max.
        """
        try:
            from datetime import datetime, timedelta
            from backend.models import ResearchRun, Setting

            # Default OFF: auto-start must be an explicit opt-in.
            auto_setting = Setting.query.filter_by(key="rag_autoresearch_auto_enabled").first()
            auto_enabled = (auto_setting.value.lower() == "true") if auto_setting else False
            if not auto_enabled:
                return

            window_setting = Setting.query.filter_by(key="autoresearch_nightly_window").first()
            window = (window_setting.value if window_setting else "") or "01:00-06:00"
            try:
                start_s, end_s = window.split("-")
                start_h, start_m = (int(x) for x in start_s.strip().split(":"))
                end_h, end_m = (int(x) for x in end_s.strip().split(":"))
            except ValueError:
                logger.warning(f"Bad autoresearch_nightly_window {window!r}; using 01:00-06:00")
                start_h, start_m, end_h, end_m = 1, 0, 6, 0

            now = datetime.now()
            minutes = now.hour * 60 + now.minute
            w_start, w_end = start_h * 60 + start_m, end_h * 60 + end_m
            in_window = (w_start <= minutes < w_end) if w_start <= w_end \
                else (minutes >= w_start or minutes < w_end)
            if not in_window:
                return

            # One run per night: anything created in the last 20h counts.
            recent = ResearchRun.query.filter(
                ResearchRun.created_at > datetime.utcnow() - timedelta(hours=20)
            ).first()
            if recent is not None:
                return

            remaining_min = (w_end - minutes) if w_start <= w_end else \
                ((w_end - minutes) % (24 * 60))
            budget_hours = max(0.5, remaining_min / 60.0)

            logger.info(f"Nightly window open — kicking off research run "
                        f"({budget_hours:.1f}h budget)")
            from backend.services.research_run_service import get_research_run_service
            get_research_run_service().kickoff(
                mode="unified", budget_hours=budget_hours, trigger="nightly"
            )
        except Exception as e:
            logger.error(f"Autoresearch nightly check failed: {e}")

    @celery_app.task(name="autoresearch.on_index_complete")
    def on_index_complete():
        """Called after indexing completes — marks eval set as potentially stale."""
        try:
            from backend.services.rag_autoresearch_service import get_autoresearch_service
            svc = get_autoresearch_service()
            if svc.eval_harness.is_stale():
                logger.info("Eval set is stale after indexing — will regenerate on next run")
        except Exception as e:
            logger.error(f"Post-index eval check failed: {e}")

    @celery_app.task(
        name="autoresearch.execute_run",
        bind=True,
        acks_late=True,
        reject_on_worker_lost=True,
        # Per-task limits override the worker's 40-minute default so a 6–12h
        # overnight run is owned by this task for its full wall-clock budget.
        time_limit=13 * 3600,
        soft_time_limit=12 * 3600 + 1800,
    )
    def execute_run_task(self, run_id):
        """Long-lived owner of one ResearchRun. Replaces the daemon thread
        that died on worker_max_tasks_per_child=50."""
        from backend.services.research_run_service import get_research_run_service
        svc = get_research_run_service()
        try:
            svc.execute_run(run_id)
        except Exception:
            logger.exception("autoresearch.execute_run crashed for %s", run_id)
            try:
                svc._finalize_crashed(run_id)
            except Exception:
                logger.exception("Failed to finalize crashed run %s", run_id)
            raise


def schedule_autoresearch_tasks(celery_app):
    """Register autoresearch Beat schedule."""
    from backend.celery_beat_gates import gate_beat_entries

    celery_app.conf.beat_schedule.update({
        "autoresearch-idle-check": {
            "task": "autoresearch.check_idle",
            # Window check, not idle polling — every 10 min is plenty.
            "schedule": 600.0,
        },
    })
    # Not sent at all while the Settings toggle is off (the task's own check
    # stays as the second line of defence).
    gate_beat_entries(celery_app, {"autoresearch-idle-check": "autoresearch"})
