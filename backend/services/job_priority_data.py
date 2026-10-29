"""Priority classes, the job types in each, and the timing the job arbiter uses.

Read by backend/services/job_arbiter.py. This file is the one place a job
type's default class is declared; the arbiter, the Activity page and the tests
all read it from here.

Three classes, highest first:

    interactive  chat, voice, and anything the person is watching for right now
    yours        renders and batches the person started and left to run
    background   indexing, autoresearch, self-improvement, scheduled jobs

Interactive work heads the GPU line. Background work pauses at its safe
points while interactive work runs, and a waiting background job gains
priority with age so it always runs eventually (BACKGROUND_AGING_S). Nothing
here evicts a job that is already running.

All of this applies only while the job order setting is on
(settings_utils.get_job_priority_enabled); with it off the arbiter is a
pass-through and the order is whatever each queue does by itself.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class PriorityClass(str, Enum):
    INTERACTIVE = "interactive"
    YOURS = "yours"
    BACKGROUND = "background"


# rank orders the classes (lower runs first); label and help are what the
# Activity page shows.
CLASS_INFO: dict[PriorityClass, dict] = {
    PriorityClass.INTERACTIVE: {
        "rank": 0,
        "label": "Interactive",
        "help": "Chat, voice and anything you are waiting on. First in line for the graphics card; background work pauses for it.",
    },
    PriorityClass.YOURS: {
        "rank": 1,
        "label": "Yours",
        "help": "Renders and batches you started. Ahead of background work; a running one is never stopped.",
    },
    PriorityClass.BACKGROUND: {
        "rank": 2,
        "label": "Background",
        "help": "Indexing, autoresearch, self-improvement and scheduled jobs. Pauses while you chat and moves up the longer it waits.",
    },
}

# Score distance between two adjacent classes. A waiting item's score is
# rank * CLASS_STEP, minus its age credit (background only), minus whatever the
# person moved it by; the lowest score is admitted first.
CLASS_STEP = 100.0


# Every job type the arbiter knows. ``class`` is the default class; ``label``
# names it on the Activity page; ``nightly`` marks work that starts on its own
# overnight and waits for an idle box (IDLE_FOR_NIGHTLY_S); ``requeues`` marks
# a job whose GPU claim is asked once and, when refused, retried by sending
# itself again with the same id (see RETRY_FRESH_S), so its place is kept.
JOB_TYPES: dict[str, dict] = {
    # Interactive
    "chat": {"class": PriorityClass.INTERACTIVE, "label": "Chat reply"},
    "voice_chat": {"class": PriorityClass.INTERACTIVE, "label": "Voice reply"},
    "chat_picture": {"class": PriorityClass.INTERACTIVE, "label": "Picture for chat"},
    # Yours
    "video_batch": {"class": PriorityClass.YOURS, "label": "Video Gen batch"},
    "image_batch": {"class": PriorityClass.YOURS, "label": "Image Gen batch"},
    "picture": {"class": PriorityClass.YOURS, "label": "Picture"},
    "infographic": {"class": PriorityClass.YOURS, "label": "Infographic"},
    "character_pictures": {"class": PriorityClass.YOURS, "label": "Character pictures"},
    "storyboard": {"class": PriorityClass.YOURS, "label": "Storyboard pictures"},
    "film_crew": {"class": PriorityClass.YOURS, "label": "Film Crew render"},
    "music_video": {"class": PriorityClass.YOURS, "label": "Music video clip", "requeues": True},
    "song": {"class": PriorityClass.YOURS, "label": "Song"},
    "video_render": {"class": PriorityClass.YOURS, "label": "Video render", "requeues": True},
    "upscale": {"class": PriorityClass.YOURS, "label": "Upscale"},
    "lora_train": {"class": PriorityClass.YOURS, "label": "LoRA training"},
    "training": {"class": PriorityClass.YOURS, "label": "Model training"},
    "gpu_job": {"class": PriorityClass.YOURS, "label": "GPU job"},
    # Background
    "indexing": {"class": PriorityClass.BACKGROUND, "label": "Indexing"},
    "autoresearch": {"class": PriorityClass.BACKGROUND, "label": "Autoresearch", "nightly": True},
    "self_improvement": {"class": PriorityClass.BACKGROUND, "label": "Self-improvement"},
    "scheduled_task": {"class": PriorityClass.BACKGROUND, "label": "Scheduled task"},
    "outreach": {"class": PriorityClass.BACKGROUND, "label": "Outreach"},
}

# A gpu_session names its job by the id it claims the GPU with, or failing
# that by its slot id (the video editor's op id is a bare file stem; its slot is
# video_render:editor_<stem>). These cover every prefix
# job_operation_gate._HOLDER_ID_LABELS names holders by (a test keeps the two in
# step); the first match wins, so a longer prefix sharing a start comes first.
OP_ID_PREFIX_TYPES: tuple[tuple[str, str], ...] = (
    ("VideoBatch", "video_batch"),
    ("ImageBatch", "image_batch"),
    ("InfographicBatch", "infographic"),
    ("blueprint_", "image_batch"),
    ("upload_", "image_batch"),
    ("stills_chat_", "chat_picture"),    # stills_pipeline with source="chat"
    ("stills_", "picture"),
    ("gen_", "picture"),
    ("chat_", "chat_picture"),           # chat_edit_, chat_anim_, chat_qwen_edit_, chat_pulid_
    ("infographic_", "infographic"),
    ("char_", "character_pictures"),
    ("storyboard_", "storyboard"),
    ("prod_", "film_crew"),
    ("mv_storyboard", "storyboard"),      # mv_storyboards_ and mv_storyboard_
    ("mv_", "music_video"),
    ("video:music3:", "song"),
    ("editor_", "video_render"),
    ("router:", "video_render"),
    ("upscale:", "upscale"),
)

# When the id names nothing above, the JobKind the session claims with decides.
KIND_TYPES: dict[str, str] = {
    "training": "training",
    "lora_train": "lora_train",
    "video_render": "gpu_job",
}


# --- Timing -------------------------------------------------------------------
#
# Starting values, not measurements. Each says what it trades so the next
# person can change it with a reason.

# A waiting background job's age credit grows linearly to one full class step
# over this many seconds: after 30 minutes in the GPU line it ranks with Yours
# work queued at the moment it started waiting, so it goes ahead of renders
# queued later but not of renders that were already waiting. It never reaches
# the interactive class. At safe points the same span ends the grace below: a
# background job held back this long waits for each turn itself only, so a
# long conversation cannot hold it off for good.
BACKGROUND_AGING_S = 1800.0

# How long background work stays held after an interactive turn ends. A
# conversation's next message usually follows within seconds; resuming a
# background job between turns only to pause it again costs a model reload.
INTERACTIVE_GRACE_S = 20.0

# Nightly jobs start only after this long with no interactive or Yours work
# running or waiting.
IDLE_FOR_NIGHTLY_S = 600.0

# An interactive ticket older than this stops pausing background work. Covers a
# request that hangs without ending (its process still alive), so it cannot hold
# background work off indefinitely. Long agent runs in chat stay well inside it.
INTERACTIVE_MAX_S = 1800.0

# A waiting ticket whose job has not asked for its turn within this long is
# dropped: its wait loop ended without saying so (a fail-fast caller that was
# refused, a retry loop that gave up).
WAITING_STALE_S = 30.0

# A waiting ticket is only offered the head of the line while it is asking.
# Wait loops poll about once a second (the gate's wait, gpu_session_when_free,
# which also keeps its ticket fresh through backoff sleeps); a ticket that has
# gone quiet for longer is skipped, so it cannot hold the line while asleep.
HEAD_FRESH_S = 5.0

# The same window for a job type marked ``requeues``: a music-video clip and a
# video editor render come back after a 12 s Celery countdown
# (music_video_tasks, video_render_tasks). Any other caller that asks once
# leaves the line when it is refused, so it never holds the head unasked.
RETRY_FRESH_S = 15.0

# How often a job paused at a safe point, or queued behind another, asks again.
POLL_S = 1.0


def job_type_info(job_type: str) -> dict:
    """The declaration for ``job_type``; an unknown type is Yours work."""
    return JOB_TYPES.get(job_type) or {"class": PriorityClass.YOURS, "label": job_type.replace("_", " ")}


def default_class(job_type: str) -> PriorityClass:
    return PriorityClass(job_type_info(job_type)["class"])


def class_rank(cls: "PriorityClass | str") -> int:
    return int(CLASS_INFO[PriorityClass(cls)]["rank"])


def job_type_for_session(kind, op_id: str, slot_id: Optional[str] = None) -> str:
    """The job type of a gpu_session: from the id it claims with, else from its
    slot id without the leading ``<kind>:``, else from its kind."""
    candidates = [str(op_id or "")]
    if slot_id:
        candidates.append(str(slot_id).split(":", 1)[-1])
    for text in candidates:
        for prefix, job_type in OP_ID_PREFIX_TYPES:
            if text.startswith(prefix):
                return job_type
    kind_name = str(getattr(kind, "value", kind) or "")
    return KIND_TYPES.get(kind_name, "gpu_job")


def is_nightly(job_type: Optional[str]) -> bool:
    return bool(job_type and JOB_TYPES.get(job_type, {}).get("nightly"))


def requeues(job_type: Optional[str]) -> bool:
    return bool(job_type and JOB_TYPES.get(job_type, {}).get("requeues"))
