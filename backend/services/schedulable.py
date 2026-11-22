"""What can be scheduled from one line of text, and when "tonight at 3" is.

The Jobs page's "Schedule a job" form and the chat tool ``schedule_job`` both
take a feature and a prompt. ``FEATURES`` declares, once, how each feature
turns that into a job the night shift can hold (backend/services/night_shift.py):
most run the feature's own chat tool with the prompt as its main argument.
A page that has more to say than one prompt (a video batch with its model,
size and seeds) schedules its own request through /api/night-shift/jobs.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

NIGHT_SHIFT = "night_shift"
AT = "at"


def _slug(text: str, fallback: str) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())[:6]
    return "-".join(words)[:48] or fallback


@dataclass(frozen=True)
class Feature:
    """One thing a person can schedule by describing it.

    ``tool`` is the chat tool run at the scheduled time, with the prompt as
    ``param``. ``extra`` adds the arguments that tool requires beyond the
    prompt (a file name). ``kind`` instead names a hand-off kind
    (backend/services/job_handoff.py) and ``body`` builds its request.
    ``words`` are how a chat message names the feature. ``person_only``
    features run tools that need a person's approval each time: the Jobs page
    may schedule them (the click is the approval), chat may not.
    """

    id: str
    label: str
    page: str
    hint: str
    tool: Optional[str] = None
    param: str = "prompt"
    extra: Optional[Callable[[str], dict]] = None
    kind: Optional[str] = None
    body: Optional[Callable[[str], dict]] = None
    words: tuple = ()
    person_only: bool = False


FEATURES: dict[str, Feature] = {f.id: f for f in (
    Feature("image", "Image", "/batch-images", "What the image shows",
            tool="generate_image",
            words=("image", "images", "picture", "pictures", "photo", "illustration", "drawing")),
    Feature("video", "Video clip", "/video", "What happens in the clip",
            tool="generate_video", words=("video", "videos", "clip", "clips")),
    Feature("infographic", "Infographic", "/infographic", "What the infographic explains",
            kind="infographic", body=lambda p: {"scene": p, "quantity": 1},
            words=("infographic", "infographics")),
    Feature("music", "Instrumental song", "/audio", "The sound: genre, instruments, mood, tempo",
            kind="music", body=lambda p: {"style_prompt": p, "duration_s": 60, "instrumental_only": True},
            words=("song", "songs", "music", "track", "beat", "jingle")),
    Feature("speech", "Narration", "/audio", "The words to speak",
            tool="generate_speech", param="text", words=("narration", "voiceover", "voice-over", "speech")),
    Feature("film_crew", "Film Crew production", "/film-crew", "The screenplay or scene list",
            kind="film_crew",
            body=lambda p: {"name": (p.strip().splitlines() or ["Film"])[0][:80], "script_text": p},
            words=("film crew", "short film", "screenplay")),
    Feature("file", "Written file", "/file-generation", "What the file should contain",
            tool="generate_file", param="content_description",
            extra=lambda p: {"filename": f"{_slug(p, 'scheduled')}.md"},
            words=("report", "document", "article", "write-up", "summary")),
    Feature("csv", "CSV table", "/content-library", "The columns and the rows the table holds",
            tool="generate_csv", param="data_description",
            extra=lambda p: {"filename": f"{_slug(p, 'scheduled')}.csv"},
            words=("csv", "spreadsheet", "table")),
    Feature("code", "Code file", "/code-editor", "What the code should do",
            tool="codegen", param="instructions",
            extra=lambda p: {"output_filename": f"{_slug(p, 'scheduled')}.py"},
            words=("code", "script", "program")),
    Feature("research", "Web research", "/chat", "What to search the web for",
            tool="web_search", param="query", words=("research", "web search", "search the web")),
    Feature("agent_task", "Agent screen task", "/agents", "What the agent should do on its screen",
            tool="agent_task_execute", param="task", words=("agent task", "screen task")),
    Feature("swarm", "Coding swarm", "/swarm", "What the swarm should build or investigate",
            tool="launch_swarm", param="goal", words=("coding swarm", "swarm"), person_only=True),
    Feature("self_improvement", "Self-improvement task", "/self-improvement", "What to investigate and fix",
            tool="submit_improvement", param="description",
            words=("self-improvement", "self improvement"), person_only=True),
)}

# Chat tools a scheduled job may run. Tools outside this set (desktop control,
# shell commands, posting) are not run unattended whatever asks for it.
SCHEDULABLE_TOOLS = frozenset(f.tool for f in FEATURES.values() if f.tool) | {
    "generate_animation", "generate_music_video", "generate_bulk_csv", "index_document",
    "analyze_website", "outreach_draft_post",
}
PERSON_ONLY_TOOLS = frozenset(f.tool for f in FEATURES.values() if f.tool and f.person_only)


# Arguments a scheduled call always carries. In chat generate_image renders
# while the person waits and refuses when the GPU is busy; scheduled, it goes
# on the image queue, which takes its turn behind whatever is rendering.
UNATTENDED_ARGUMENTS = {"generate_image": {"wait_for_result": False}}


def tool_request(tool: str, arguments: dict) -> dict:
    """The saved request of a scheduled tool call."""
    parameters = {k: v for k, v in (arguments or {}).items() if v is not None}
    return {"tool_name": tool, "parameters": {**parameters, **UNATTENDED_ARGUMENTS.get(tool, {})}}


def _check_video(arguments: dict) -> Optional[str]:
    from backend.tools.image_tools import VideoGeneratorTool

    if not str(arguments.get("prompt") or "").strip():
        return "The clip needs a prompt."
    names = ("model", "aspect_ratio", "duration_s", "duration_frames", "num_inference_steps", "first_image",
             "last_image", "reference_images", "reference_audio", "reference_clips", "speed_profile", "style")
    audio = str(arguments.get("audio")).lower() in ("1", "true", "yes")
    _, error = VideoGeneratorTool.resolve_request(
        str(arguments["prompt"]), audio=audio, **{k: arguments[k] for k in names if arguments.get(k) is not None})
    return error


def _check_image(arguments: dict) -> Optional[str]:
    if not str(arguments.get("prompt") or "").strip():
        return "The image needs a prompt."
    model = str(arguments.get("model") or "auto").strip().lower()
    try:
        from backend.services.batch_image_generator import get_batch_image_generator

        generator = get_batch_image_generator().image_generator
    except Exception:  # noqa: BLE001 - no generator here: the run itself answers
        return None
    if generator is None:
        return None
    # Cast characters render on the base model of their own LoRA.
    if arguments.get("subject_ids"):
        return None
    return generator.missing_model_message(model)


# Tools whose arguments can be checked without running them: the reason a
# saved call would be refused, or None.
ARGUMENT_CHECKS: dict[str, Callable[[dict], Optional[str]]] = {
    "generate_video": _check_video,
    "generate_image": _check_image,
}


def check_arguments(tool: str, arguments: dict) -> None:
    """Raise ValueError with the reason the tool would refuse these arguments
    when it runs, so a job is refused when it is saved and not hours later."""
    check = ARGUMENT_CHECKS.get(tool)
    error = check(arguments or {}) if check else None
    if error:
        raise ValueError(error)


def feature_job(feature_id: str, prompt: str, options: Optional[dict] = None) -> dict:
    """``{type, variant, request, name}`` for a feature and a prompt.
    Raises ValueError for an unknown feature or an empty prompt."""
    feature = FEATURES.get(str(feature_id or "").strip().lower())
    if feature is None:
        raise ValueError(f"feature must be one of {sorted(FEATURES)}")
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError(f"{feature.label} needs a prompt: {feature.hint.lower()}")
    options = {k: v for k, v in (options or {}).items() if v is not None}
    name = f"{feature.label}: {prompt[:70]}"
    if feature.kind:
        return {"type": feature.kind, "variant": None, "request": {**feature.body(prompt), **options},
                "name": name}
    arguments = {**(feature.extra(prompt) if feature.extra else {}), **options, feature.param: prompt}
    return {"type": "tool_call", "variant": None, "request": tool_request(feature.tool, arguments), "name": name}


def catalog() -> list[dict]:
    """The features and kinds a page can offer, for GET /api/night-shift/kinds."""
    from backend.services.job_handoff import HANDOFFS

    return [{"id": f.id, "label": f.label, "page": f.page, "hint": f.hint,
             "tool": f.tool, "kind": f.kind or "tool_call"} for f in FEATURES.values()] + [
        {"id": f"page:{key}", "label": kind.label, "page": kind.page, "hint": None, "tool": None,
         "kind": key, "page_only": True}
        for key, kind in HANDOFFS.items()
        if key != "tool_call" and not any(f.kind == key for f in FEATURES.values())
    ]


# --- when ---------------------------------------------------------------------

_NIGHT_RE = re.compile(
    r"\b(?:(?:to|on|for|in|during|into)\s+(?:the\s+|tonight'?s\s+)?night\s*shift"
    r"|night\s*shift|overnight|while\s+i(?:'m|\s+am)?\s+(?:sleep(?:ing)?|asleep|away|out|gone)"
    r"|when\s+(?:the\s+)?(?:machine|computer|pc|gpu)\s+is\s+(?:idle|free)|after\s+i\s+(?:leave|go\s+to\s+bed))\b",
    re.IGNORECASE,
)
_IN_RE = re.compile(
    r"\bin\s+(?P<n>\d+(?:\.\d+)?|an?|half\s+an)\s*(?P<unit>minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE)
_CLOCK = (r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)?")
_AT_RE = re.compile(
    r"\b(?:(?P<day1>today|tonight|tomorrow)\s+)?(?:at|@|around|by)\s+" + _CLOCK
    + r"(?:\s+(?P<day2>today|tonight|tomorrow))?(?=$|[\s,.;!?])", re.IGNORECASE)
# "tomorrow 9am", "today 14:30": a day and a clock time with no "at" between.
_DAY_CLOCK_RE = re.compile(
    r"\b(?P<day1>today|tonight|tomorrow)\s+(?P<h>\d{1,2})(?:(?::(?P<m>\d{2}))\s*(?P<ap>a\.?m\.?|p\.?m\.?)?"
    r"|\s*(?P<ap2>a\.?m\.?|p\.?m\.?))(?=$|[\s,.;!?])", re.IGNORECASE)
_NOON_RE = re.compile(
    r"\b(?:(?P<day1>today|tonight|tomorrow)\s+)?at\s+(?P<word>noon|midnight)(?:\s+(?P<day2>today|tonight|tomorrow))?\b",
    re.IGNORECASE)
_DAYPART_RE = re.compile(r"\b(?P<day>tomorrow|this)\s+(?P<part>morning|afternoon|evening|night)\b", re.IGNORECASE)
_ISO_RE = re.compile(r"\b(?P<iso>\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2})\b")
_TONIGHT_RE = re.compile(r"\b(?:later\s+)?tonight\b", re.IGNORECASE)
# Hours a bare day part stands for.
_DAYPART_HOUR = {"morning": 9, "afternoon": 14, "evening": 19, "night": 22}


@dataclass(frozen=True)
class When:
    when: str                                   # "night_shift" or "at"
    at: Optional[datetime.datetime] = None      # local time, for "at"
    span: tuple = (0, 0)                        # where the message says it
    # False for wording a scene description also uses ("a street at 3am",
    # "the city tonight"); such a time schedules only when asked to.
    plain: bool = True


def parse_when(text: str, now: Optional[datetime.datetime] = None) -> Optional[When]:
    """The time a message schedules something for, or None when it names none.

    "in 2 hours", "at 3am", "tomorrow at 9:30", "tomorrow morning",
    "2026-11-22 03:00" give a clock time; "tonight", "overnight", "on the
    night shift", "while I'm asleep" give the night shift. A clock time
    without a day is the next time the clock reads it.
    """
    text = text or ""
    now = (now or datetime.datetime.now()).replace(second=0, microsecond=0)

    m = _ISO_RE.search(text)
    if m:
        try:
            return When(AT, datetime.datetime.fromisoformat(m.group("iso").replace(" ", "T")), m.span())
        except ValueError:
            pass
    m = _IN_RE.search(text)
    if m:
        n = m.group("n").lower()
        amount = 0.5 if n.startswith("half") else 1.0 if n in ("a", "an") else float(n)
        minutes = amount * (60 if m.group("unit").lower().startswith("h") else 1)
        return When(AT, now + datetime.timedelta(minutes=minutes), m.span())
    m = _NOON_RE.search(text)
    if m:
        day = (m.group("day1") or m.group("day2") or "").lower()
        hour = 12 if m.group("word").lower() == "noon" else 0
        at = now.replace(hour=hour, minute=0)
        if day == "tomorrow" or (hour == 0 and day != "today") or at <= now:
            at += datetime.timedelta(days=1)
        return When(AT, at, m.span(), plain=day == "tomorrow")
    m = _DAY_CLOCK_RE.search(text) or _AT_RE.search(text)
    if m:
        groups = m.groupdict()
        hour, minute = int(m.group("h")), int(m.group("m") or 0)
        ap = (groups.get("ap") or groups.get("ap2") or "").lower().replace(".", "")
        day = (groups.get("day1") or groups.get("day2") or "").lower()
        if hour <= 23 and minute <= 59:
            if ap == "pm" and hour < 12:
                hour += 12
            elif ap == "am" and hour == 12:
                hour = 0
            elif not ap and day == "tonight" and hour < 12 and hour >= 5:
                hour += 12  # "tonight at 9" is the evening
            at = now.replace(hour=hour, minute=minute)
            if day == "tomorrow":
                at += datetime.timedelta(days=1)
            elif at <= now:
                at += datetime.timedelta(days=1)
            return When(AT, at, m.span(), plain=day == "tomorrow")
    m = _DAYPART_RE.search(text)
    if m and not (m.group("day").lower() == "this" and m.group("part").lower() == "night"):
        at = now.replace(hour=_DAYPART_HOUR[m.group("part").lower()], minute=0)
        if m.group("day").lower() == "tomorrow" or at <= now:
            at += datetime.timedelta(days=1)
        return When(AT, at, m.span())
    m = _NIGHT_RE.search(text)
    if m:
        return When(NIGHT_SHIFT, None, m.span())
    m = _TONIGHT_RE.search(text)
    if m:
        return When(NIGHT_SHIFT, None, m.span(), plain=False)
    return None


def parse_time_argument(value: Any, now: Optional[datetime.datetime] = None) -> When:
    """A tool's ``time`` argument: an ISO local time, or words parse_when reads.
    Raises ValueError for a time that cannot be read or has passed."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("a time is required, e.g. '2026-11-22T03:00', 'in 2 hours' or 'tomorrow at 9am'")
    now = now or datetime.datetime.now()
    if re.search(r"(?i)\b(?:yesterday|ago|last\s+(?:night|week|month))\b", text):
        raise ValueError(f"{text!r} is in the past")
    found = None
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        found = When(AT, parsed)
    except ValueError:
        found = parse_when(text if re.match(r"(?i)^\s*(?:in|at|@|tomorrow|today|tonight|this)\b", text)
                           else f"at {text}", now) or parse_when(text, now)
    if found is None:
        raise ValueError(f"could not read the time {text!r}; use e.g. '2026-11-22T03:00', 'in 2 hours' "
                         "or 'tomorrow at 9am'")
    if found.when == AT and found.at <= now:
        raise ValueError(f"{found.at:%Y-%m-%d %H:%M} has already passed")
    return found


def to_utc(local: datetime.datetime) -> datetime.datetime:
    """A naive local time as the naive UTC the tasks table holds."""
    return local.astimezone().astimezone(datetime.timezone.utc).replace(tzinfo=None)


# --- a chat message that schedules -------------------------------------------

_SCHEDULE_VERB_RE = re.compile(
    r"\b(?:schedule|queue\s+up|queue|line\s+up|set\s+up|plan|add)\b", re.IGNORECASE)
_CREATE_VERB_RE = re.compile(
    r"\b(?:make|create|generate|render|draw|write|produce|compose|record|run|start|train|launch|do|build)\b",
    re.IGNORECASE)
# Left in front of the prompt once the verb, the feature and the time are cut out.
_LEAD_RE = re.compile(
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+|i\s+want\s+you\s+to\s+|i'?d\s+like\s+(?:you\s+)?to\s+)*"
    r"(?:(?:schedule|queue\s+up|queue|line\s+up|set\s+up|plan|add|make|create|generate|render|draw|write|produce|"
    r"compose|record|run|start|do|build)\s+)?(?:me\s+)?(?:an?\s+|the\s+|some\s+|one\s+)?", re.IGNORECASE)
_JOIN_RE = re.compile(r"^(?:of|about|for|on|showing|with|that|which|to|:|-|,)\s+", re.IGNORECASE)
_TAIL_RE = re.compile(
    r"(?:\s+(?:to\s+run|to\s+start|and\s+run\s+it|to\s+be\s+(?:made|rendered|done)|(?:for|to|on|in|at|by)(?:\s+the)?))?"
    r"[\s,.;:!-]*$", re.IGNORECASE)
_COUNT_RE = re.compile(r"(?:^|\s)(?P<n>\d{1,2})\s*$")
MAX_COUNT = 20


def _feature_in(text: str) -> Optional[tuple[Feature, tuple]]:
    """The feature a message names first by its longest wording, and where."""
    best = None
    for feature in FEATURES.values():
        for word in feature.words:
            m = re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.IGNORECASE)
            if m and (best is None or m.start() < best[1][0]
                      or (m.start() == best[1][0] and m.end() > best[1][1])):
                best = (feature, m.span())
    return best


@dataclass(frozen=True)
class ScheduleRequest:
    feature: str
    prompt: str
    when: str
    at: Optional[datetime.datetime]
    count: int = 1


def read_schedule_request(message: str, now: Optional[datetime.datetime] = None,
                          commanded: bool = False) -> Optional[ScheduleRequest]:
    """A request for something to be made later, read into a feature, a
    prompt and a time; None when it is not one.

    ``commanded`` is the text after ``/schedule``: the command is the ask, so
    it needs only a feature, and with no time it means the night shift.

    Without the command the message must say when (parse_when), name a
    feature, and ask for it: a scheduling verb anywhere, or a create verb
    before the feature. "What's on tonight?" and "the report we wrote at 3pm"
    name no job to make. A time that could be part of the scene ("a diner at
    3am") schedules only with a scheduling verb or when the message opens
    with it.
    """
    text = " ".join((message or "").split())
    if not text or len(text) > 1200 or (text.endswith("?") and not commanded):
        return None
    when = parse_when(text, now)
    if when is None:
        if not commanded:
            return None
        when = When(NIGHT_SHIFT)
        rest = text
    else:
        rest = (text[:when.span[0]] + " " + text[when.span[1]:]).strip()
    found = _feature_in(rest)
    if found is None:
        return None
    feature, span = found
    before = rest[:span[0]]
    if not commanded:
        asked = bool(_SCHEDULE_VERB_RE.search(rest))
        if not (asked or _CREATE_VERB_RE.search(before)):
            return None
        if not (asked or when.plain or when.span[0] == 0):
            return None
    lead = _LEAD_RE.sub("", before, count=1).strip()
    count = 1
    counted = _COUNT_RE.search(lead)
    if counted:
        count = max(1, min(int(counted.group("n")), MAX_COUNT))
        lead = lead[:counted.start()].strip()
    prompt = (lead + " " + _JOIN_RE.sub("", rest[span[1]:].strip())).strip()
    prompt = _TAIL_RE.sub("", prompt).strip()
    prompt = _JOIN_RE.sub("", prompt).strip()
    if len(prompt) < 3:
        return None
    return ScheduleRequest(feature.id, prompt, when.when, when.at, count)


# What chat's /schedule command takes after the word.
COMMAND = "/schedule"
_COMMAND_RE = re.compile(r"^\s*/schedule\b[ \t]*(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
_COMMAND_ACTION_RE = re.compile(
    r"^(?:(?P<list>list|status|queue)|(?P<verb>remove|delete|cancel|run|start|pause|hold)\s+(?:job\s+)?#?(?P<job>\d+)"
    r"(?:\s+now)?)\s*$", re.IGNORECASE)
COMMAND_USAGE = (
    "Use /schedule with what to make and when, for example:\n"
    "- /schedule a video of a red fox running through snow tonight\n"
    "- /schedule 3 images of a paper boat on a pond at 3am\n"
    "- /schedule a report on local AI trends tomorrow at 9\n"
    "- /schedule an infographic about how RAG works in 2 hours\n"
    "With no time it goes on the night shift. It can make: "
    + ", ".join(f.label.lower() for f in FEATURES.values() if not f.person_only) + ".\n"
    "/schedule list shows what is scheduled; /schedule remove 91, /schedule run 91 and "
    "/schedule pause 91 act on one job."
)


def read_command(message: str, now: Optional[datetime.datetime] = None) -> Optional[dict]:
    """A ``/schedule`` chat message as the tool call it stands for:
    ``{"tool", "params"}``, or ``{"usage": text}`` when it names nothing that
    can be scheduled. None for a message that is not the command."""
    m = _COMMAND_RE.match(message or "")
    if not m:
        return None
    rest = m.group("rest").strip()
    if not rest:
        return {"tool": "scheduled_jobs", "params": {"action": "list"}}
    action = _COMMAND_ACTION_RE.match(rest)
    if action:
        if action.group("list"):
            return {"tool": "scheduled_jobs", "params": {"action": "list"}}
        verb = action.group("verb").lower()
        name = ("remove" if verb in ("remove", "delete", "cancel")
                else "run_now" if verb in ("run", "start") else "pause")
        return {"tool": "scheduled_jobs", "params": {"action": name, "job": int(action.group("job"))}}
    if re.match(r"(?i)^(?:help|\?)$", rest):
        return {"usage": COMMAND_USAGE}
    request = read_schedule_request(rest, now, commanded=True)
    if request is None:
        return {"usage": COMMAND_USAGE}
    params: dict = {"feature": request.feature, "prompt": request.prompt, "when": request.when}
    if request.at is not None:
        params["time"] = request.at.strftime("%Y-%m-%dT%H:%M")
    if request.count > 1:
        params["count"] = request.count
    return {"tool": "schedule_job", "params": params}
