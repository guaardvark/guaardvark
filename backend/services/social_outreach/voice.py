"""The account owner's own writing voice, for outreach drafts.

Without it the drafter writes in the model's voice, which reads as a model.
``data/social_outreach/VOICE.md`` is a sheet a person keeps about how they
write. When it exists, every draft is written under it; when it does not,
nothing here changes a draft. The file is read again whenever it changes.

The sheet has up to three parts, split by these headings:

    (text before any heading)   how the person writes, as instructions
    ## Never                    one phrase or character per line that they
                                never write; ``re:`` starts a regular
                                expression. Checked after drafting.
    ## Examples                 real things they wrote, separated by lines
                                holding only ``---``. A few are picked at
                                random for each draft, so drafts do not all
                                lean on the same ones.
"""

from __future__ import annotations

import logging
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VOICE_PATH = Path(
    os.environ.get("GUAARDVARK_ROOT") or Path(__file__).resolve().parents[3]
) / "data" / "social_outreach" / "VOICE.md"

# Examples shown to the drafter per draft.
EXAMPLES_PER_DRAFT = 5
# A draft that repeats a run of this many words from an example copied it;
# the examples are there for rhythm, and a lifted phrase turns up in every draft.
COPIED_RUN_WORDS = 5


@dataclass
class Voice:
    rules: str = ""
    never: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


_cache: tuple[float, Optional[Voice]] = (-1.0, None)


def parse(text: str) -> Voice:
    parts = {"rules": [], "never": [], "examples": []}
    section = "rules"
    for line in text.splitlines():
        heading = line.strip().lower()
        if heading == "## never":
            section = "never"
        elif heading == "## examples":
            section = "examples"
        else:
            parts[section].append(line)
    never = [l.strip() for l in parts["never"] if l.strip() and not l.strip().startswith("#")]
    examples = [e.strip() for e in re.split(r"(?m)^---\s*$", "\n".join(parts["examples"])) if e.strip()]
    return Voice(rules="\n".join(parts["rules"]).strip(), never=never, examples=examples)


def load() -> Optional[Voice]:
    """The voice sheet, or None when there is none."""
    global _cache
    try:
        mtime = VOICE_PATH.stat().st_mtime
    except OSError:
        return None
    if _cache[0] == mtime:
        return _cache[1]
    try:
        voice = parse(VOICE_PATH.read_text(encoding="utf-8"))
    except OSError as e:
        logger.warning("VOICE.md unreadable: %s", e)
        return None
    if not (voice.rules or voice.examples or voice.never):
        voice = None
    _cache = (mtime, voice)
    return voice


def block(rng: Optional[random.Random] = None) -> str:
    """The part of the system message that sets the voice; "" without a sheet."""
    voice = load()
    if voice is None:
        return ""
    rng = rng or random.Random()
    lines = [
        "--- VOICE ---",
        "Write as the person described here writes. This replaces \"write in your own voice\": "
        "your own voice reads as a language model, and the draft must read as this person typed it.",
        "The voice decides how the draft is worded, not what it says. What it says still follows "
        "everything above: it answers what this particular post is about, with a specific from the "
        "post, and it claims nothing the sheet does not back, including about what this person does "
        "or uses. A draft that could be pasted under a different post is a failed draft.",
    ]
    if voice.rules:
        lines += ["", voice.rules]
    if voice.never:
        lines += ["", "They never write any of these: " + " | ".join(n for n in voice.never if not n.startswith("re:"))]
    if voice.examples:
        picked = rng.sample(voice.examples, min(EXAMPLES_PER_DRAFT, len(voice.examples)))
        lines += [
            "",
            "Things they really wrote, under other posts, for the rhythm only. Do not reuse a "
            "sentence, a phrase of three or more words, or a fact from them:",
        ]
        lines += [f"[{i}] {example}" for i, example in enumerate(picked, 1)]
    lines.append("--- END OF VOICE ---")
    return "\n".join(lines)


def _words(text: str) -> list[str]:
    # Links are shared on purpose; they are not copied wording.
    return re.findall(r"[a-z0-9']+", re.sub(r"\S*(?:\.com|\.be|\.io|\.ai|://)\S*", " ", text.lower()))


def _example_runs(voice: Voice) -> set:
    runs = getattr(voice, "_runs", None)
    if runs is None:
        runs = set()
        for example in voice.examples:
            words = _words(example)
            runs.update(
                " ".join(words[i:i + COPIED_RUN_WORDS]) for i in range(len(words) - COPIED_RUN_WORDS + 1)
            )
        voice._runs = runs
    return runs


def copied(text: str) -> list[str]:
    """Runs of ``COPIED_RUN_WORDS`` words the draft shares with an example,
    overlapping runs joined into the whole lifted phrase."""
    voice = load()
    if voice is None or not voice.examples or not text:
        return []
    runs = _example_runs(voice)
    words = _words(text)
    phrases: list[str] = []
    start = None
    for i in range(len(words) - COPIED_RUN_WORDS + 2):
        hit = i <= len(words) - COPIED_RUN_WORDS and " ".join(words[i:i + COPIED_RUN_WORDS]) in runs
        if hit and start is None:
            start = i
        elif not hit and start is not None:
            phrases.append(" ".join(words[start:i - 1 + COPIED_RUN_WORDS]))
            start = None
    return phrases


def hits(text: str) -> list[str]:
    """What in ``text`` the person would not have written: the ``## Never``
    entries it contains, and phrases lifted from the examples."""
    voice = load()
    if voice is None or not text:
        return []
    found = [f"copied from an example: {phrase}" for phrase in copied(text)]
    lowered = text.lower()
    for entry in voice.never:
        if entry.startswith("re:"):
            try:
                if re.search(entry[3:].strip(), text, re.IGNORECASE):
                    found.append(entry)
            except re.error as e:
                logger.warning("VOICE.md: bad pattern %r: %s", entry, e)
        elif entry.lower() in lowered:
            found.append(entry)
    return found
