"""Heading-aware splitting for Markdown documents.

Markdown had no dedicated ingest branch: it fell through to SimpleDirectoryReader,
which yields one flat blob per file. Every heading -- the one piece of structure
markdown reliably carries -- was discarded before chunking.

Splitting on headings instead gives each section a breadcrumb ("Design > Storage >
Vector store"), which is what lets a retrieved chunk say where in a document it
came from, and gives a small local model enough context to use the chunk without
a follow-up call it may never make.
"""

import logging
import os
import re
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".mdx"}

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")

# A section is bounded by headings, so a long run of prose with no headings stays
# whole -- one real archive file produced a single 270,845-character "section".
# Handing that to the hierarchical chunker is what turned 495 KB of source into
# 353,154 nodes. Cap the size and split the overflow on paragraph boundaries.
MAX_SECTION_CHARS = int(os.environ.get("GUAARDVARK_MD_MAX_SECTION_CHARS", "20000"))


def is_enabled() -> bool:
    return os.environ.get("GUAARDVARK_MD_SECTIONS_ENABLED", "true").lower() == "true"


def supports(file_extension: str) -> bool:
    return is_enabled() and file_extension.lower() in SUPPORTED_EXTENSIONS


def split_sections(text: str) -> List[dict]:
    """Split markdown into {heading, heading_path, level, text} sections.

    Headings inside fenced code blocks are ignored -- a '# comment' in a shell
    snippet is not a document section.
    """
    sections: List[dict] = []
    stack: List[tuple] = []          # (level, title)
    cur_lines: List[str] = []
    cur_heading: Optional[str] = None
    cur_level = 0
    cur_path = ""
    in_fence = False

    def _emit(heading, path, level, body, part=None):
        title = f"{'#' * level} {heading}\n{body}" if heading else body
        entry = {
            "heading": heading,
            "heading_path": path,
            "level": level,
            "text": title.strip(),
        }
        if part is not None:
            entry["part"] = part
        sections.append(entry)

    def flush():
        body = "\n".join(cur_lines).strip()
        if not body and not cur_heading:
            return
        if MAX_SECTION_CHARS <= 0 or len(body) <= MAX_SECTION_CHARS:
            _emit(cur_heading, cur_path, cur_level, body)
            return

        # Oversized: split on blank lines so paragraphs stay intact, and keep the
        # breadcrumb on every part so a retrieved chunk still names its section.
        parts, buf, size = [], [], 0
        for para in body.split("\n\n"):
            if size and size + len(para) > MAX_SECTION_CHARS:
                parts.append("\n\n".join(buf))
                buf, size = [], 0
            buf.append(para)
            size += len(para) + 2
        if buf:
            parts.append("\n\n".join(buf))

        logger.info(
            "Section %r is %d chars; splitting into %d parts (cap %d)",
            (cur_heading or "(no heading)")[:60], len(body), len(parts), MAX_SECTION_CHARS,
        )
        for i, chunk in enumerate(parts):
            # Only the first part carries the heading line, so the text is not
            # repeated; the breadcrumb metadata carries the context for the rest.
            _emit(cur_heading if i == 0 else None, cur_path, cur_level, chunk, part=i)

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            cur_lines.append(line)
            continue

        m = None if in_fence else _HEADING.match(line)
        if m:
            flush()
            cur_lines = []
            cur_level = len(m.group(1))
            cur_heading = m.group(2).strip()
            while stack and stack[-1][0] >= cur_level:
                stack.pop()
            stack.append((cur_level, cur_heading))
            cur_path = " > ".join(t for _, t in stack)
        else:
            cur_lines.append(line)

    flush()
    return [s for s in sections if s["text"]]


def load_documents(file_path: str, filename: str, LlamaDocument) -> Optional[List[Any]]:
    """One LlamaDocument per markdown section. None if unreadable or empty."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception as e:
        logger.warning("Markdown read failed for %s: %s", filename, e)
        return None

    if not text.strip():
        return None

    sections = split_sections(text)
    if not sections:
        return None

    documents = []
    for i, sec in enumerate(sections):
        meta = {
            "source_filename": filename,
            "file_path": str(file_path),
            "parsed_by": "markdown_sections",
            "section_index": i,
            "section_count": len(sections),
        }
        if sec["heading"]:
            meta["heading"] = sec["heading"]
            meta["heading_path"] = sec["heading_path"]
            meta["heading_level"] = sec["level"]
        documents.append(LlamaDocument(text=sec["text"], metadata=meta))

    logger.info("Markdown parsed %s into %d section(s)", filename, len(documents))
    return documents
