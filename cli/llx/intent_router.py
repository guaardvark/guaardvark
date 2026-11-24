"""Resolve REPL input to CLI commands before falling through to chat LLM.

Extended with many natural-language shortcuts for local agentic actions so the
REPL feels like Claude Code / Cline / Cursor / Grok when doing software work.
"""

from __future__ import annotations

import re
import shlex
import shutil
from pathlib import Path

from llx.command_catalog import (
    BARE_ONLY_COMMANDS,
    BARE_OR_NUMBER_COMMANDS,
    COMMAND_TREE,
    ONE_WORD_COMMANDS,
    OPTIONS_ONLY_COMMANDS,
)

_AUDIO_EXT = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac")
_SCRIPT_EXT = (".txt", ".md", ".fountain", ".fdx")
# "create an image upload endpoint" is a coding request, not a render: the word
# after image/video (one other word allowed between) names a piece of software.
# The chat engine applies the same rule to typed chat (_MEDIA_AS_SOFTWARE_PATTERN).
_NOT_SOFTWARE = (
    r"(?!(?:(?!(?:of|with|for|in|on|at|a|an|the|that|where|showing|about|from)\b)[a-z0-9-]+[\s-]+)?"
    r"(?:endpoints?|routes?|apis?|components?|services?|uploads?|uploaders?|carousels?|sliders?|viewers?|"
    r"players?|queues?|pipelines?|handlers?|modules?|class(?:es)?|functions?|fields?|columns?|tables?|"
    r"forms?|widgets?|tags?|elements?|loaders?|caches?|parsers?|schemas?|databases?|librar(?:y|ies)|"
    r"plugins?|features?|scripts?|pages?|galler(?:y|ies)|pickers?|previews?|thumbnails?|converters?|"
    r"optimi[sz]ers?|compress(?:or|ion)|processing|editors?|downloaders?|scrapers?|classifiers?)\b)"
)


def _first_word(text: str) -> str:
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    return parts[0] if parts else ""


def _path_exists(token: str, cwd: Path | None, *, file_only: bool = False) -> bool:
    if not token:
        return False
    path = Path(token).expanduser()
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    try:
        return path.is_file() if file_only else path.exists()
    except OSError:
        return False


def _starts_with_program(tail: str, cwd: Path | None) -> bool:
    """True when the tail's first word is a program on PATH or a script that exists.

    "run pytest -q" and "run ./build.sh" qualify; "run through the plan" does not.
    A word with a slash is a path, resolved against the REPL's folder rather than
    looked up on PATH, because that folder is where /run executes it.
    """
    first = _first_word(tail)
    if not first:
        return False
    if "/" not in first and shutil.which(first):
        return True
    return _path_exists(first, cwd, file_only=True)


def _names_test_target(tail: str, cwd: Path | None) -> bool:
    """True when the tail starts with an existing path (pytest node ids allowed)."""
    first = _first_word(tail)
    return _path_exists(first.split("::", 1)[0], cwd)


def _read_target(tail: str) -> str:
    """The file a read line names: the tail without /read's offset and limit."""
    words = tail.split()
    while words and words[-1].isdigit():
        words.pop()
    return " ".join(words)


# English verbs that are also local commands. Typed without a slash they run the
# command only when the rest of the line has the command's shape; otherwise the
# line is chat: "fix my cover letter wording", "find a good pizza place nearby",
# "show me how closures work", "list three ideas for dinner", "tasks for today".
# The Unix spellings (cat, grep, ls, dir) run as before.
_TODO_WORDS = frozenset(COMMAND_TREE["todo"]) | {"ls", "complete", "finish"}


def _has_command_shape(verb: str, cmd: str, tail: str, cwd: Path | None) -> bool:
    if cmd == "read" and verb in ("read", "show", "view"):
        return _path_exists(_read_target(tail), cwd, file_only=True)
    if cmd == "edit":
        return _path_exists(_first_word(tail), cwd, file_only=True)
    if cmd == "grep" and verb in ("search", "find"):
        words = tail.split()
        return len(words) == 1 or _path_exists(words[-1], cwd)
    if cmd == "ls" and verb == "list":
        return not tail or _path_exists(tail, cwd)
    if cmd == "todo":
        return not tail or tail.split()[0].lower() in _TODO_WORDS
    return True


def _fits_command_tree(parts: list[str]) -> bool:
    """Whether a line starting with a command name reads as that command.

    A command with subcommands needs one of them (or an option) next: "files
    list" and "generate csv report" run, "generate ideas for a party" and
    "rules for a board game" are chat. The limits in command_catalog apply too.
    """
    cmd, rest = parts[0].lower(), parts[1:]
    if cmd in BARE_ONLY_COMMANDS:
        return not rest
    if cmd in BARE_OR_NUMBER_COMMANDS:
        return not rest or (len(rest) == 1 and rest[0].isdigit())
    if cmd in OPTIONS_ONLY_COMMANDS:
        return all(word.startswith("-") for word in rest)
    if cmd in ONE_WORD_COMMANDS:
        if cmd == "help":
            return not rest or (len(rest) == 1 and rest[0].lower().lstrip("/") in COMMAND_TREE)
        return len(rest) <= 1
    subcommands = COMMAND_TREE.get(cmd)
    if subcommands and rest and not rest[0].startswith("-"):
        return rest[0].lower() in subcommands
    return True


def _music_video_args(tail: str) -> list[str]:
    args = ["create"]
    song = None
    rest = tail or ""
    for token in rest.split():
        cleaned = token.strip(".,;:\"'")
        if cleaned.isdigit() or cleaned.lower().endswith(_AUDIO_EXT) or "/" in cleaned:
            song = cleaned
            rest = rest.replace(token, " ", 1)
            break
    style = " ".join(rest.split()).strip()
    if song:
        args += ["--song", song]
    if style:
        args += ["--style", style]
    return args


def _film_crew_args(tail: str) -> list[str]:
    args = ["create"]
    text = (tail or "").strip()
    if not text:
        return args
    first = text.split()[0]
    cleaned = first.strip(".,;:\"'")
    if cleaned.lower().endswith(_SCRIPT_EXT) or "/" in cleaned:
        args += ["--file", cleaned]
        return args
    args += ["--script", text]
    return args

# (pattern, command name, fixed sub-args or None to use parsed tail)
_NL_INTENT_RULES: list[tuple[re.Pattern[str], str, list[str] | None]] = [
    (re.compile(r"^(?:list|show|what are)\s+(?:the\s+)?agents?\s*$", re.I), "agents", ["list"]),
    (re.compile(r"^agents?\s+list\s*$", re.I), "agents", ["list"]),
    (re.compile(r"^(?:system\s+)?status\s*$", re.I), "status", []),
    (re.compile(r"^health(?:\s+check)?\s*$", re.I), "health", []),
    (
        re.compile(
            r"^(?:outreach\s+)?(?:comment|scout|draft).*(?:youtube|reddit|discord|comfyui|offline\s*ai|local\s*(?:llm|ai)).+$",
            re.I,
        ),
        "outreach",
        None,
    ),
    (re.compile(r"^outreach(?:\s+(.+))?$", re.I), "outreach", None),
    (re.compile(r"^(?:run|execute)\s+agent\s+(.+)$", re.I), "agents", None),

    # Image / video generation (must precede generic COMMAND_TREE "generate" fallback)
    (
        re.compile(
            r"^(?:generate|create|make|draw)\s+(?:an?\s+)?(?:image|picture|photo)\s+"
            + _NOT_SOFTWARE + r"(?:of\s+)?(.+)$",
            re.I,
        ),
        "imagine",
        None,
    ),
    # Music-video / Film Crew before the generic "make a video" rule.
    (
        re.compile(
            r"^(?:generate|create|make|produce)\s+(?:a\s+)?music[\s-]?video(?:\s+(?:of|from|for|using|with))?\s*(.*)$",
            re.I,
        ),
        "music-video",
        None,
    ),
    (
        re.compile(
            r"^(?:start|run|create|make)\s+(?:the\s+)?film[\s-]?crew(?:\s+(?:on|for|with))?\s*(.*)$",
            re.I,
        ),
        "film-crew",
        None,
    ),
    (
        re.compile(r"^(?:film|produce)\s+(?:this\s+)?script\s*(.*)$", re.I),
        "film-crew",
        None,
    ),
    (
        re.compile(r"^(?:generate|create|make)\s+(?:an?\s+)?video\s+" + _NOT_SOFTWARE + r"(?:of\s+)?(.+)$", re.I),
        "video",
        None,
    ),

    (re.compile(r"^(?:list|show)\s+plugins?\s*$", re.I), "plugins", ["list"]),
    (re.compile(r"^what(?:'s| is) using the gpu\s*$", re.I), "gpu", ["status"]),
    (re.compile(r"^gpu(?:\s+status)?\s*$", re.I), "gpu", ["status"]),
    (re.compile(r"^start\s+(comfyui|ollama|upscaling)\s*$", re.I), "plugins", None),
    (re.compile(r"^play\s+(?:that\s+)?(?:voice|audio|clip)\s+(.+)$", re.I), "audio", None),

    # Local coding actions (new in this improvement)
    (re.compile(r"^(?:ls|list(?:\s+files?)?(?:\s+in)?|dir)\b\s*(.*)$", re.I), "ls", None),
    (re.compile(r"^(?:cd|chdir)\s+(.+)$", re.I), "cd", None),
    (re.compile(r"^pwd\s*$", re.I), "pwd", []),
    (re.compile(r"^(?:cat|read|show|view)\s+(?:file\s+)?(.+)$", re.I), "read", None),
    (re.compile(r"^(?:grep|search|find)\s+(.+)$", re.I), "grep", None),
    (re.compile(r"^(?:edit|fix|update|implement|change)\s+(.+)$", re.I), "edit", None),
    # run/test are checked again in resolve_repl_line: English that only starts
    # with these verbs ("run through the plan", "check the weather") is chat.
    (re.compile(r"^(?:run|exec|execute|sh)\s+(.+)$", re.I), "run", None),
    (re.compile(r"^(?:test|pytest|check)\b\s*(.*)$", re.I), "test", None),
    (re.compile(r"^(?:todo|tasks?)\b\s*(.*)$", re.I), "todo", None),
    (re.compile(r"^(?:context|status|state|where am i)\s*$", re.I), "context", []),
    (re.compile(r"^(?:suggest|what tools?|recommend tools?)\s*$", re.I), "suggest", []),
    (re.compile(r"^(?:init|initialize|setup project|create guaardvark)\s*$", re.I), "init", []),
    (re.compile(r"^(?:analyze|review|inspect)\s+(?:the\s+)?(?:site|project|folder|css|styling|build)\s*(.*)$", re.I), "analyze", None),
    (re.compile(r"^(?:suggest|improve)\s+(?:css|styling|style)\s*(.*)$", re.I), "analyze", None),

    # Load skill / dynamic instructions (like loading this session's skill files)
    (re.compile(r"^(?:load|use|apply)\s+(?:the\s+)?skill\s+(.+?)(?:\s+(?:file|md|instructions?))?\s*$", re.I), "load", None),
    (re.compile(r"^(?:load|read)\s+(?:skill|instructions?)\s+(.+)$", re.I), "load", None),
]


def resolve_repl_line(line: str, cwd: Path | None = None) -> tuple[str, list[str]] | None:
    """Return (command, args) for SlashRouter, or None to use chat.

    cwd is the REPL's working folder; script and test paths named on the line
    must exist there (default: the process working directory).
    """
    raw = line.strip()
    if not raw or raw.startswith("/"):
        return None

    if raw.lower().startswith("guaardvark "):
        raw = raw[len("guaardvark ") :].strip()
    if not raw:
        return None

    for pattern, cmd, fixed_args in _NL_INTENT_RULES:
        match = pattern.match(raw)
        if not match:
            continue
        if fixed_args is not None:
            return cmd, list(fixed_args)
        if cmd == "outreach":
            # Prefer capture group when present; else full raw line is the NL intent.
            try:
                captured = (match.group(1) or "").strip()
            except IndexError:
                captured = ""
            payload = captured or raw
            if payload.lower().startswith("outreach "):
                payload = payload[9:].strip()
            return cmd, [payload] if payload else []
        tail = (match.group(1) or "").strip()
        if cmd == "run" and not _starts_with_program(tail, cwd):
            return None
        if cmd == "test" and tail and not _names_test_target(tail, cwd):
            return None
        if not _has_command_shape(raw.split()[0].lower(), cmd, tail, cwd):
            return None
        if cmd in ("ls", "read", "grep", "edit", "run", "test", "cd", "todo"):
            # pass the whole tail as single arg string; slash handler will shlex if needed
            return cmd, [tail] if tail else []
        if cmd == "analyze":
            return "analyze", [tail] if tail else []
        if cmd == "load":
            return "load", [tail] if tail else []
        if cmd in ("imagine", "video"):
            return cmd, [tail] if tail else []
        if cmd == "music-video":
            return cmd, _music_video_args(tail)
        if cmd == "film-crew":
            return cmd, _film_crew_args(tail)
        if cmd == "plugins":
            captured = (match.group(1) or "").strip() if match.lastindex else ""
            return cmd, ["start", captured] if captured else ["list"]
        if cmd == "audio":
            return cmd, ["play", tail] if tail else ["voices"]
        return cmd, ["run", tail] if tail else []

    try:
        parts = shlex.split(raw)
    except ValueError:
        return None

    if not parts:
        return None

    cmd = parts[0].lower()
    if cmd in COMMAND_TREE and _fits_command_tree(parts):
        return cmd, parts[1:]

    return None
