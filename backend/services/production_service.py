"""Production pipeline state machine. DB-persisted, crash-resumable.

Owns the transition graph for a Production through its stages:
  draft → screenwriting → casting → cinematography → storyboard_gen
  → awaiting_approval → rendering → complete

The lifecycle plumbing (atomic advance, fail, resume, GPU gate) lives in
``PipelineService`` — this module only declares Production's stage graph and its
domain ``create()``. ``VALID_TRANSITIONS`` / ``STAGE_TO_AGENT`` / ``TERMINAL_STATUSES``
remain importable here for back-compat.
"""
from __future__ import annotations

from backend.models import Production
from backend.services.pipeline_service import (
    PipelineService,
    TERMINAL_STATUSES,  # noqa: F401  re-exported for back-compat
    _coerce_error,      # noqa: F401  re-exported for back-compat
)


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


class ProductionService(PipelineService):
    """Coordinates state transitions on Production rows.

    Take a SQLAlchemy session in the constructor (Flask-SQLAlchemy's `db.session`
    works). Optionally wire a `gate` (JobOperationGate) for GPU exclusivity on
    generation-heavy stages.
    """

    model_cls = Production
    valid_transitions = VALID_TRANSITIONS
    stage_to_agent = STAGE_TO_AGENT
    task_namespace = "production"

    # P2: dispatch_agent and resume_all in PipelineService now wire
    # ensure_plugins_for_stage(self.task_namespace, row.current_stage)
    # for auto-sequencing of plugins per Film Crew stages (see STAGE_PLUGIN_REQUIREMENTS).

    def create(self, *, name: str, script_text: str, project_id: int | None,
               settings: dict | None = None) -> Production:
        # settings_json carries per-production choices the agents read later,
        # such as video_model (which family renders the shots).
        p = Production(
            name=name,
            script_text=script_text,
            project_id=project_id,
            status="draft",
            current_stage="draft",
            settings_json=dict(settings or {}),
        )
        self.s.add(p)
        self.s.commit()
        return p
