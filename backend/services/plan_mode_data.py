"""What plan mode works with, as data: step kinds, limits, the compose schema
and the wording the model reads.

Plan mode researches an objective with read-only tools, writes a plan, and
runs it only after the person approves. The limits here are sized for the
small local models a fresh install runs (gemma4:e2b by default) at an 8192
token context; change one only with the measurement behind it
(scripts/eval_plan_mode.py) recorded next to the number.
"""

import hashlib
import json
from typing import Any, Dict, FrozenSet

# Research turns before the plan is written. Each turn is one model call
# that may run research tools; a plan's research rarely needs more than a
# search, a read and a check.
PLAN_RESEARCH_MAX_ITERATIONS = 6

# Steps a plan may have. A longer plan is cut to its first steps and says so;
# small models lose the thread past this, and a person reviews it faster.
PLAN_MAX_STEPS = 8

# How much of the research the compose call is shown, in characters. The
# compose call runs at the model's loaded context; this leaves room for the
# schema, the instructions and the answer.
COMPOSE_RESEARCH_CHARS = 6000
COMPOSE_HISTORY_CHARS = 1500
COMPOSE_REPLY_CHARS = 2500

# Summary of a finished step handed to the steps after it.
STEP_SUMMARY_CHARS = 600

# Lowest measured pass rate (lower confidence bound) at which plan mode is
# offered on a model. Set from the first yardstick run; a model with no
# measurement is offered with a "not measured yet" note.
PLAN_MODE_FLOOR = 0.8

# Kinds of step a plan may contain. Each says what the step does, which turn
# routes are expected to carry it (a route outside the set is shown as a
# warning on the card), and options the executor adds when it runs the step.
STEP_KINDS: Dict[str, Dict[str, Any]] = {
    "write": {
        "description": "Write text for the person in the reply: an answer, a summary, a draft, a list.",
        "routes": {"chat", "social"},
        "options": {},
    },
    "file": {
        "description": "Create a file (document, spreadsheet, CSV, code file) in Guaardvark's outputs.",
        "routes": {"chat", "file_generation", "direct_tool"},
        "options": {},
    },
    "code_change": {
        "description": "Change code in the project folder or in Guaardvark's own source.",
        "routes": {"chat", "direct_tool"},
        "options": {},
    },
    "image": {
        "description": "Make a picture, or edit one.",
        "routes": {"new_image", "edit_image", "named_image", "image_retry", "edit_retry", "chat", "direct_tool"},
        "options": {},
    },
    "video": {
        "description": "Make a video clip, a music video or a Film Crew short.",
        "routes": {"video", "music_video", "film_crew", "chat", "direct_tool"},
        "options": {},
    },
    "audio": {
        "description": "Make music, a sound effect or spoken narration.",
        "routes": {"chat", "direct_tool"},
        "options": {},
    },
    "desktop": {
        "description": "Do something on the agent's screen: open, click, type, browse.",
        "routes": {"screen_task", "chat"},
        "options": {"agent_screen_active": True},
    },
    "lookup": {
        "description": "Look something up (documents, the web, the system) and use what was found.",
        "routes": {"chat", "workstation", "direct_tool"},
        "options": {},
    },
    "memory": {
        "description": "Save, change or forget something in Guaardvark's memory.",
        "routes": {"chat", "direct_tool"},
        "options": {},
    },
    "other": {
        "description": "Any other action, with the tool it uses named.",
        "routes": None,  # any route
        "options": {},
    },
}

PLAN_KINDS: FrozenSet[str] = frozenset({"answer", "question", "plan"})

# The JSON the compose call must return (an Ollama structured-output schema).
# Short fields and enums keep a small model on the rails.
COMPOSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": sorted(PLAN_KINDS)},
        "question": {"type": "string"},
        "goal": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "action": {"type": "string"},
                    "kind": {"type": "string", "enum": sorted(STEP_KINDS)},
                    "tools": {"type": "array", "items": {"type": "string"}},
                    "done_when": {"type": "string"},
                },
                "required": ["title", "action", "kind"],
            },
        },
        "checks": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["kind"],
}

# Added to the research turn's context, next to the user's message.
PLAN_MODE_RESEARCH_NOTE = (
    "Plan mode is on: you may only look things up now. Nothing that changes anything runs "
    "until the person approves a plan. Use the tools you have to find out what the request "
    "needs (read the files, search the documents, check the system). If the request is a "
    "question, answer it. If it is unclear, ask one short question. If it asks for something "
    "to be done, made or changed, say in a few sentences what you found that the work "
    "depends on (files, settings, names, values); the plan is written after your reply. Do "
    "not say it is done, because nothing has been done yet."
)

COMPOSE_SYSTEM = (
    "You turn a request and the research done for it into JSON for plan mode.\n"
    "kind: \"answer\" when the request is a question and the research reply answers it; "
    "\"question\" when you need something from the person before you can plan (put it in "
    "question); \"plan\" when the request asks for something to be done, made, changed, "
    "saved, sent or run.\n"
    "For a plan: goal is one sentence of what the person gets. steps are in the order they "
    "run, at most {max_steps}; each has a short title, an action written in plain words (not "
    "code, not a function call) as the instruction you would follow to do that step on its "
    "own (name the files, prompts, settings and values the research found; never invent a "
    "path or a name), a kind from the list, the "
    "tools it uses from the list, and done_when, how you will know the step worked. checks "
    "are how the person can confirm the result. risks are what could go wrong or what the "
    "step changes that cannot be undone. questions are anything you assumed and the person "
    "should confirm.\n"
    "Step kinds:\n{kinds}\n"
    "Tools a step may use:\n{tools}"
)


def kinds_text() -> str:
    return "\n".join(f"- {name}: {spec['description']}" for name, spec in STEP_KINDS.items())


def wording_sha() -> str:
    """A short hash of everything the model reads in plan mode, so a measured
    pass rate is tied to the wording it was measured with."""
    blob = json.dumps(
        [PLAN_MODE_RESEARCH_NOTE, COMPOSE_SYSTEM, COMPOSE_SCHEMA,
         {k: v["description"] for k, v in STEP_KINDS.items()}],
        sort_keys=True,
    )
    return hashlib.sha1(blob.encode()).hexdigest()[:12]
