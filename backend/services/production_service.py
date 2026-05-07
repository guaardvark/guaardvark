"""Production pipeline state machine. DB-persisted, crash-resumable.

Owns the transition graph for a Production through its stages:
  draft → screenwriting → casting → cinematography → storyboard_gen
  → awaiting_approval → rendering → complete

Every transition is a single DB commit. Re-dispatching at a stage other than
the agent's expected predecessor is a no-op — this is what gives us safe
idempotency under crash recovery and accidental double-fires.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import Production


VALID_TRANSITIONS: dict[str, str] = {
    "draft": "screenwriting",
    "screenwriting": "casting",
    "casting": "cinematography",
    "cinematography": "storyboard_gen",
    "storyboard_gen": "awaiting_approval",
    "awaiting_approval": "rendering",
    "rendering": "complete",
}


# Maps a current_stage to the agent that resumes work there. None means
# the stage is user-gated (no auto-resume on boot).
STAGE_TO_AGENT: dict[str, str | None] = {
    "draft": "screenwriter",          # never reached at boot — draft is pre-pipeline
    "screenwriting": "screenwriter",
    "casting": None,                   # user-driven
    "cinematography": "cinematographer",
    "storyboard_gen": "storyboard_artist",
    "awaiting_approval": None,         # user-gated
    "rendering": "editor",
}


TERMINAL_STATUSES = {"complete", "failed"}


class ProductionService:
    """Coordinates state transitions on Production rows.

    Take a SQLAlchemy session in the constructor (Flask-SQLAlchemy's `db.session`
    works). Optionally wire a `gate` (JobOperationGate) for GPU exclusivity on
    generation-heavy stages.
    """

    def __init__(self, session: Session, gate=None):
        self.s = session
        self.gate = gate

    # --- Lifecycle ---------------------------------------------------------

    def create(self, *, name: str, script_text: str, project_id: int | None) -> Production:
        p = Production(
            name=name,
            script_text=script_text,
            project_id=project_id,
            status="draft",
            current_stage="draft",
            settings_json={},
        )
        self.s.add(p)
        self.s.commit()
        return p

    # --- State machine -----------------------------------------------------

    def advance_if_predecessor(self, prod_id: int, *, expected_predecessor: str) -> bool:
        """Idempotent stage advance. Returns True iff the transition happened.

        Used by agent dispatch so it's safe against double-fire and crash-resume
        race conditions: if a Production is no longer at `expected_predecessor`,
        nothing happens (someone else already advanced it, or it's terminal).
        """
        p = self.s.get(Production, prod_id)
        if p is None or p.current_stage != expected_predecessor:
            return False
        next_stage = VALID_TRANSITIONS.get(expected_predecessor)
        if next_stage is None:
            return False
        p.current_stage = next_stage
        # Keep status synced with the active stage so Activity/UI filters see real state.
        p.status = next_stage
        self.s.commit()
        return True

    def fail_stage(self, prod_id: int, *, stage: str, error) -> None:
        """Persist a failed-stage status and error blob. Used by agent dispatchers
        when an agent returns a non-OK AgentInvocation. Status becomes
        ``failed_<stage>`` so the boot-time resumer knows to skip it.
        """
        p = self.s.get(Production, prod_id)
        if p is None:
            return
        p.status = f"failed_{stage}"
        p.error_blob = {"stage": stage, "error": error}
        self.s.commit()

    # --- Resumability ------------------------------------------------------

    def find_non_terminal(self) -> list[Production]:
        # Exclude both raw terminal statuses ("complete"/"failed") AND
        # the per-stage failure pattern (failed_screenwriting, failed_rendering, ...).
        # Without the like-clause, failed productions get re-dispatched every boot.
        return (
            self.s.query(Production)
            .filter(
                ~Production.status.in_(list(TERMINAL_STATUSES)),
                ~Production.status.like("failed_%"),
            )
            .all()
        )

    def dispatch_agent(self, prod_id: int, agent_name: str) -> None:
        from backend.celery_app import celery
        task_name = f"production.run_{agent_name}"
        celery.send_task(task_name, args=[prod_id])

    def resume_all(self) -> int:
        """Boot-time resume. For each non-terminal Production, dispatch the agent
        responsible for its current stage. User-gated stages are skipped (the user
        will trigger the next step from the UI).

        Per-production dispatch failures are caught and logged so one bad row
        can't strand the rest. Returns the count of successful dispatches.
        """
        import logging
        log = logging.getLogger(__name__)
        count = 0
        for p in self.find_non_terminal():
            agent = STAGE_TO_AGENT.get(p.current_stage)
            if agent is None:
                continue
            try:
                self.dispatch_agent(p.id, agent)
                count += 1
            except Exception as e:
                log.warning(f"Resume failed for production {p.id} (stage={p.current_stage}): {e}")
        return count

    # --- GPU gate ----------------------------------------------------------

    def gpu_stage(self, op_id: str, fn, *args, **kwargs):
        """Wrap a GPU-using stage in the JobOperationGate (if configured).

        The gate ensures GPU-exclusive operations (LoRA training, I2V render)
        don't trample each other. If no gate is wired, runs `fn` directly.
        """
        if self.gate is None:
            return fn(*args, **kwargs)
        self.gate.acquire(op_id)
        try:
            return fn(*args, **kwargs)
        finally:
            self.gate.release(op_id)
