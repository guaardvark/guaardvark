"""What plan mode works with, as data: step kinds, limits, the compose schema
and the wording the model reads.

Plan mode researches an objective with read-only tools, writes a plan, and
runs it only after the person approves. The limits here are sized for the
local models a fresh install runs (gemma4:12b, or qwen3.5 on smaller machines) at an 8192
token context; change one only with the measurement behind it
(scripts/eval_plan_mode.py) recorded next to the number.
"""

import hashlib
import json
import re
from typing import Any, Dict, FrozenSet, Iterable, Optional

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

# Most tokens one compose call may write. The largest plan in the 2026-10-26
# yardstick runs (gemma4:12b, 96 objectives) was 2,318 characters of JSON,
# about 700 tokens; without a cap a structured-output call that loops runs
# until the context is full (one took ten minutes that day).
COMPOSE_MAX_TOKENS = 2048

# Seconds before a compose call is abandoned. A backstop for a stalled
# Ollama; COMPOSE_MAX_TOKENS is what bounds a normal call (5-30 s on
# gemma4:12b here).
COMPOSE_TIMEOUT_S = 300

# Summary of a finished step handed to the steps after it.
STEP_SUMMARY_CHARS = 600

# Lowest measured pass rate (lower confidence bound) at which plan mode is
# offered on a model. Set from the first yardstick run; a model with no
# measurement is offered with a "not measured yet" note.
PLAN_MODE_FLOOR = 0.8

# Kinds of step a plan may contain. Each says what the step does; the tools
# that do it, each with when to use it (shown to the model that writes the
# plan when the tool is registered, and used to correct a step filed under
# the wrong kind); what the research note calls the ability; which turn
# routes are expected to carry it (a route outside the set is shown as a
# warning on the card); and options the executor adds when it runs the step.
# A kind with tools listed is left out of the prompts when none of them is
# registered: chat registers no music or speech tool (backend/tools/
# tool_registry_init.py register_audio_tools is MCP-only), so chat plans
# cannot promise audio. Such a kind is named to the model as not possible
# here instead, with "elsewhere", where in Guaardvark it can be done.
STEP_KINDS: Dict[str, Dict[str, Any]] = {
    "write": {
        "description": "Write text for the person in the reply: an answer, a summary, a draft, a list.",
        "tools": {},
        "ability": "",
        "routes": {"chat", "social"},
        "options": {},
    },
    "file": {
        "description": "Create a file (document, spreadsheet, CSV, code file) in Guaardvark's outputs.",
        "tools": {"generate_file": "", "generate_csv": "a CSV", "generate_bulk_csv": "many CSV rows",
                  "codegen": "a code file"},
        "ability": "files",
        "routes": {"chat", "file_generation", "direct_tool"},
        "options": {},
    },
    "code_change": {
        "description": "Change code in the project folder or in Guaardvark's own source.",
        "tools": {"edit_code": ""},
        "ability": "code",
        "routes": {"chat", "direct_tool"},
        "options": {},
    },
    "image": {
        "description": "Make a picture, or edit one.",
        "tools": {"generate_image": "a new picture", "edit_image": "change a picture",
                  "inpaint_image": "", "outpaint_image": "", "remove_background": ""},
        "ability": "pictures",
        "routes": {"new_image", "edit_image", "named_image", "image_retry", "edit_retry", "chat", "direct_tool"},
        "options": {},
    },
    "video": {
        "description": "Make a video.",
        "tools": {"generate_video": "a clip", "generate_animation": "a short loop",
                  "generate_music_video": "a music video for a song", "start_film_crew": "a short film"},
        "ability": "video",
        "routes": {"video", "music_video", "film_crew", "chat", "direct_tool"},
        "options": {},
    },
    "audio": {
        "description": "Make music or a sound effect, or speak words aloud.",
        "tools": {"generate_music": "music or a sound effect", "generate_speech": "spoken words"},
        "ability": "music and speech",
        "elsewhere": "the Audio Studio page",
        "routes": {"chat", "direct_tool"},
        "options": {},
    },
    "desktop": {
        "description": "Do something on the agent's screen: open, click, type, browse.",
        "tools": {"agent_task_execute": "a whole task", "app_launch": "", "browser_navigate": "",
                  "gui_click": "", "gui_type": ""},
        "ability": "the agent's screen",
        "routes": {"screen_task", "chat"},
        "options": {"agent_screen_active": True},
    },
    "lookup": {
        "description": "Look something up (documents, the web, the system) and use what was found.",
        "tools": {},
        "ability": "",
        "routes": {"chat", "workstation", "direct_tool"},
        "options": {},
    },
    "memory": {
        "description": "Save, change or forget something in Guaardvark's memory.",
        "tools": {"save_memory": "", "update_memory": "", "delete_memory": ""},
        "ability": "memory",
        "routes": {"chat", "direct_tool"},
        "options": {},
    },
    "other": {
        "description": "Any other action, with the tool it uses named.",
        "tools": {},
        "ability": "",
        "routes": None,  # any route
        "options": {},
    },
}

# Screen-control tools beyond the ones listed under "desktop" belong to it too.
_SCREEN_TOOL = re.compile(r"^(agent_|gui_|app_|browser_|clipboard_)")

# Kinds whose steps only read and write text; a plan made of nothing else
# changes nothing and needs no approval.
TEXT_KINDS: FrozenSet[str] = frozenset({"write", "lookup"})


def tool_kind(tool_name: str) -> Optional[str]:
    """The step kind a tool belongs to, or None."""
    for kind, spec in STEP_KINDS.items():
        if tool_name in spec["tools"]:
            return kind
    if _SCREEN_TOOL.match(tool_name or ""):
        return "desktop"
    return None


def offered_kinds(available: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, str]]:
    """{kind: {tool: when}} for the kinds a plan can use, given the registered
    tool names (None: all). A kind whose listed tools are all missing is left out."""
    names = None if available is None else set(available)
    out = {}
    for kind, spec in STEP_KINDS.items():
        tools = {t: w for t, w in spec["tools"].items() if names is None or t in names}
        if spec["tools"] and not tools:
            continue
        out[kind] = tools
    return out


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

# Added to the research turn's context, next to the user's message;
# {abilities} lists what the registered action tools can do.
PLAN_MODE_RESEARCH_NOTE = (
    "Plan mode is on: you may only look things up now. Nothing that changes anything runs "
    "until the person approves a plan. The tools that change or make things ({abilities}) "
    "are hidden from you now and the plan will use them, so do not say you cannot do those "
    "because you do not see their tools. Use the tools you have to find out what the request "
    "needs (read the files, search the documents, check the system). If the request is a "
    "question, answer it. If it is unclear, ask one short question. If it asks for something "
    "to be done, made or changed, say in a few sentences what you found that the work "
    "depends on (files, settings, names, values); the plan is written after your reply. Do "
    "not say it is done, because nothing has been done yet."
)


def abilities(available: Optional[Iterable[str]] = None) -> str:
    """What the registered action tools can do, as the notes name it."""
    return ", ".join(STEP_KINDS[k]["ability"] for k in offered_kinds(available) if STEP_KINDS[k]["ability"])


def unavailable(available: Optional[Iterable[str]] = None, *, web: bool = True) -> str:
    """What a plan cannot do on this install: kinds none of whose tools are
    registered, and web lookups while web access is off (its default)."""
    offered = offered_kinds(available)
    out = []
    for kind, spec in STEP_KINDS.items():
        if spec["tools"] and kind not in offered and spec["ability"]:
            out.append(f"{spec['ability']} (that is done on {spec['elsewhere']})"
                       if spec.get("elsewhere") else spec["ability"])
    if not web:
        out.append("looking things up on the web (web access is off in Settings)")
    return "; ".join(out)


# Appended to the research note when something is not possible here.
UNAVAILABLE_NOTE = (
    " Not possible from this chat: {missing}. If the request needs one of these, say so "
    "plainly and say where it can be done, instead of looking for another way."
)


def research_note(available: Optional[Iterable[str]] = None, *, web: bool = True) -> str:
    note = PLAN_MODE_RESEARCH_NOTE.format(abilities=abilities(available))
    missing = unavailable(available, web=web)
    return note + (UNAVAILABLE_NOTE.format(missing=missing) if missing else "")


COMPOSE_SYSTEM = (
    "You turn a request and the research done for it into JSON for plan mode.\n"
    "kind: \"plan\" whenever the request asks for something a tool does: remembering or "
    "saving something, making a picture, a video or other media, writing or changing a file "
    "or code, using the screen, posting, sending or running something, even when it is one "
    "simple step. \"answer\" when a reply in words is all the person asked for and the "
    "research reply gives it, or when no step kind below can do what is asked. \"question\" "
    "when you need something from the person before you can plan (put it in question).\n"
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
    "{missing}"
)

# The {missing} line of COMPOSE_SYSTEM, when something is not possible here.
COMPOSE_UNAVAILABLE = (
    "\nNot possible here: {missing}. A request that needs one of these is \"answer\", not a "
    "plan built from other tools."
)

# Added to the compose input for a second try when the research reply says
# the work cannot be done or is already done, which small models say while
# the action tools are hidden from them.
COMPOSE_CORRECTION = (
    "Note: your research reply said this cannot be done, or that it is already done. Nothing "
    "runs while planning, so nothing is done yet, and the plan can use these: {abilities}. "
    "Unless the request needs something listed as not possible here, write the plan."
)


def compose_unavailable(available: Optional[Iterable[str]] = None, *, web: bool = True) -> str:
    missing = unavailable(available, web=web)
    return COMPOSE_UNAVAILABLE.format(missing=missing) if missing else ""


def kinds_text(available: Optional[Iterable[str]] = None) -> str:
    lines = []
    for kind, tools in offered_kinds(available).items():
        line = f"- {kind}: {STEP_KINDS[kind]['description']}"
        if tools:
            line += " Tools: " + ", ".join(f"{t} ({w})" if w else t for t, w in tools.items()) + "."
        lines.append(line)
    return "\n".join(lines)


def compose_schema(available: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """COMPOSE_SCHEMA with the step kinds limited to the ones offered."""
    schema = json.loads(json.dumps(COMPOSE_SCHEMA))
    schema["properties"]["steps"]["items"]["properties"]["kind"]["enum"] = sorted(offered_kinds(available))
    return schema


def wording_sha() -> str:
    """A short hash of everything the model reads in plan mode, so a measured
    pass rate is tied to the wording it was measured with."""
    blob = json.dumps(
        [PLAN_MODE_RESEARCH_NOTE, UNAVAILABLE_NOTE, COMPOSE_SYSTEM, COMPOSE_UNAVAILABLE,
         COMPOSE_CORRECTION, COMPOSE_SCHEMA, kinds_text()],
        sort_keys=True,
    )
    return hashlib.sha1(blob.encode()).hexdigest()[:12]
