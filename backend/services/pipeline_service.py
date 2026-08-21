"""Shared state-machine base for DB-persisted, crash-resumable video pipelines.

ProductionService (Film Crew) and MusicVideoService were cloned 1:1 from the same
proven swarm pattern; this module is now the single source of truth for that
lifecycle plumbing. Each pipeline subclasses ``PipelineService`` and supplies four
class attributes:

  - ``model_cls``          the SQLAlchemy row model (Production, MusicVideo, …)
  - ``valid_transitions``  current_stage → next_stage
  - ``stage_to_agent``     current_stage → resuming agent (None = user-gated)
  - ``task_namespace``     celery task prefix ("production", "music_video", …)

The per-pipeline ``create()`` and any domain helpers (cut planning, etc.) stay on
the subclass. Everything here is genre-agnostic — it only touches the lifecycle
spine (status / current_stage / error_blob) that every video kind shares.

Why the atomic UPDATE-WHERE in ``advance_if_predecessor`` matters: two Celery
workers can race under crash-resume + double-dispatch. The atomic update makes
only one win — the loser's update affects zero rows and returns False. Do NOT
simplify it to a read-then-write.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# Raw terminal statuses. Per-stage failures use the ``failed_<stage>`` pattern,
# matched separately in find_non_terminal so failed rows aren't re-dispatched.
TERMINAL_STATUSES = {"complete", "failed"}


def _coerce_error(error):
    """Make ``error`` safe for the SQLAlchemy JSON column.

    JSON-native types pass through; Exceptions and anything else get stringified —
    better to lose structure than lose the whole row to a serialization crash on
    commit (which would strand it in its non-terminal stage forever).
    """
    if error is None or isinstance(error, (str, int, float, bool, list, dict)):
        return error
    return str(error)


class PipelineService:
    """Coordinates state transitions on a pipeline row model.

    Take a SQLAlchemy session (Flask-SQLAlchemy's ``db.session`` works). Optionally
    wire a ``gate`` (JobOperationGate) for GPU exclusivity on generation-heavy
    stages. Subclasses set the four class attributes below.
    """

    #: Subclasses MUST override these four.
    model_cls = None
    valid_transitions: dict[str, str] = {}
    stage_to_agent: dict[str, "str | None"] = {}
    task_namespace: str = ""

    def __init__(self, session: Session, gate=None):
        self.s = session
        self.gate = gate

    # --- State machine -----------------------------------------------------

    def advance_if_predecessor(self, row_id: int, *, expected_predecessor: str) -> bool:
        """Atomic stage advance. Returns True iff the transition happened.

        The atomic UPDATE-WHERE (current_stage == expected_predecessor) makes only
        one of two racing workers win. Status mirrors current_stage so Activity/UI
        filters see real state.
        """
        next_stage = self.valid_transitions.get(expected_predecessor)
        if next_stage is None:
            return False
        rows = (
            self.s.query(self.model_cls)
            .filter(
                self.model_cls.id == row_id,
                self.model_cls.current_stage == expected_predecessor,
            )
            .update(
                {"current_stage": next_stage, "status": next_stage},
                synchronize_session=False,
            )
        )
        self.s.commit()
        return rows > 0

    def fail_stage(self, row_id: int, *, stage: str, error) -> None:
        """Persist a ``failed_<stage>`` status + error blob so the boot resumer skips it."""
        row = self.s.get(self.model_cls, row_id)
        if row is None:
            return
        row.status = f"failed_{stage}"
        row.error_blob = {"stage": stage, "error": _coerce_error(error)}
        self.s.commit()

    # --- Resumability ------------------------------------------------------

    def find_non_terminal(self) -> list:
        # Exclude raw terminal statuses AND the per-stage failure pattern, else
        # failed rows get re-dispatched every boot.
        # Also treat explicit "cancelled" (from user cancel on music-video etc.)
        # as terminal so resume_all and dispatch loops don't keep poking them.
        return (
            self.s.query(self.model_cls)
            .filter(
                ~self.model_cls.status.in_(list(TERMINAL_STATUSES)),
                ~self.model_cls.status.like("failed_%"),
                ~self.model_cls.status.like("cancelled%"),
                self.model_cls.status != "cancelled",
            )
            .all()
        )

    def dispatch_agent(self, row_id: int, agent_name: str) -> None:
        # P2: wire phase map – ensure plugins for the row's current stage before dispatching
        # (idempotent; uses persist_user_pref=False for auto-orchestrated paths)
        row = self.s.get(self.model_cls, row_id)
        if row:
            try:
                from backend.services.plugin_bridge import ensure_plugins_for_stage
                ensure_plugins_for_stage(self.task_namespace, row.current_stage)
            except Exception:
                log.warning(
                    "Phase ensure failed for %s %s stage=%s (non-fatal)",
                    self.task_namespace, row_id, row.current_stage,
                )
            try:
                # P3: GPU model phase prep in dispatch path too
                from backend.services.gpu_memory_orchestrator import get_orchestrator
                get_orchestrator().prepare_for_stage(self.task_namespace, row.current_stage)
            except Exception:
                log.warning(
                    "GPU stage prepare failed for %s %s stage=%s (non-fatal)",
                    self.task_namespace, row_id, row.current_stage,
                )
        from backend.celery_app import celery
        celery.send_task(f"{self.task_namespace}.run_{agent_name}", args=[row_id])

    def resume_all(self) -> int:
        """Boot-time resume. Dispatch the agent for each non-terminal row's stage.
        User-gated stages (agent is None) are skipped. Per-row dispatch failures
        are caught and logged so one bad row can't strand the rest. Returns the
        count of successful dispatches.
        """
        count = 0
        for row in self.find_non_terminal():
            agent = self.stage_to_agent.get(row.current_stage)
            if agent is None:
                continue
            try:
                # P2 phase hook: ensure before dispatch (dispatch also ensures, but resume path benefits from early)
                try:
                    from backend.services.plugin_bridge import ensure_plugins_for_stage
                    ensure_plugins_for_stage(self.task_namespace, row.current_stage)
                except Exception:
                    log.warning(
                        "Phase ensure failed for %s %s stage=%s (non-fatal)",
                        self.task_namespace, row.id, row.current_stage,
                    )
                try:
                    # P3: enhance GPU orch with stage/phase model prep (parallel to plugin phases)
                    from backend.services.gpu_memory_orchestrator import get_orchestrator
                    get_orchestrator().prepare_for_stage(self.task_namespace, row.current_stage)
                except Exception:
                    log.warning(
                        "GPU stage prepare failed for %s %s stage=%s (non-fatal)",
                        self.task_namespace, row.id, row.current_stage,
                    )
                self.dispatch_agent(row.id, agent)
                count += 1
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "Resume failed for %s %s (stage=%s): %s",
                    self.task_namespace, row.id, row.current_stage, e,
                )
        return count

    # --- GPU gate ----------------------------------------------------------

    def gpu_stage(self, op_id: str, fn, *args, kind=None, **kwargs):
        """Wrap a GPU-using stage in the JobOperationGate (if configured).

        The gate ensures GPU-exclusive operations (LoRA training, I2V render,
        storyboard image gen) don't trample each other on the shared GPU. If no
        gate is wired, runs ``fn`` directly. ``op_id`` is the native_id of this
        holder; ``kind`` is the JobKind slot (defaults to VIDEO_RENDER). On
        contention ``gpu_exclusive`` raises GpuBusyError — the caller fails the
        stage cleanly rather than double-loading the GPU.
        """
        if self.gate is None:
            return fn(*args, **kwargs)
        from backend.services.job_types import JobKind
        from backend.services.gpu_resource_policy import gpu_session
        if kind is None:
            kind = JobKind.VIDEO_RENDER
        with gpu_session(kind, op_id, cross_process=True):
            return fn(*args, **kwargs)
