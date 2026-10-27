"""What kind of file a path is, declared once.

- ``code``: source a person reads and edits. The code tools list, grep and read
  it on demand; whether it is also embedded is the ``code_retrieval`` setting.
- ``prose``: documents, markup and configuration the knowledge index embeds.
- ``media``: images, audio and video. Stored and shown, never read as text.
- ``build_artifact``: compiled output, and anything inside a dependency or cache
  folder. Never read, and left out of folder imports.

A file whose first 8 KB hold a NUL byte is binary whatever its extension says;
``looks_binary`` is that check (the one git and grep use). Formats a reader
parses from the file itself (PDF, Office, spreadsheets, EPUB) are binary by
design and go to that reader instead.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePath
from typing import Union

PathLike = Union[str, os.PathLike]

CODE = "code"
PROSE = "prose"
MEDIA = "media"
BUILD_ARTIFACT = "build_artifact"

CODE_EXTENSIONS = frozenset({
    ".py", ".pyx", ".pyi",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".java", ".kt", ".kts", ".scala",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hxx",
    ".cs", ".go", ".rs", ".swift", ".dart",
    ".php", ".rb", ".pl", ".pm", ".lua", ".r", ".m", ".matlab", ".vim",
    ".sh", ".bash", ".zsh",
    ".sql",
    ".css", ".scss", ".sass",
    ".vue", ".svelte",
    ".dockerfile",
})

# Parsed from the file itself, so binary bytes are expected: Docling or the PDF
# and Office readers, the spreadsheet extractor, and the EPUB and HWP readers
# (indexing_service.get_documents_from_file).
PARSED_DOCUMENT_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".pptx", ".ppt", ".pptm",
    ".xlsx", ".xls", ".xlsm", ".xlsb",
    ".epub", ".hwp",
})

# Markup and configuration. Embedded like documents; the upload path has always
# flagged them is_code_file so the Code page lists them next to the source.
MARKUP_CONFIG_EXTENSIONS = frozenset({
    ".html", ".htm", ".xml", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf",
})

MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown", ".mdx"})

IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico", ".heic", ".avif",
})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".mkv", ".avi"})
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

BUILD_ARTIFACT_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".class", ".o", ".obj", ".so", ".dll", ".dylib", ".a", ".lib", ".exe",
})

# Folders whose contents are generated or installed, never authored.
BUILD_DIR_NAMES = frozenset({
    "__pycache__", "node_modules", "venv", ".venv", "dist", ".git",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".next",
})

# Filenames with no extension that are source.
CODE_FILENAMES = frozenset({"dockerfile", "makefile", "rakefile", "gemfile", "procfile"})


def _ext(path: PathLike) -> str:
    return os.path.splitext(str(path))[1].lower()


def in_build_dir(path: PathLike) -> bool:
    """True when any folder in ``path`` is a dependency, cache or VCS folder."""
    return any(part in BUILD_DIR_NAMES for part in PurePath(str(path).replace("\\", "/")).parts[:-1])


def kind_of(path: PathLike) -> str:
    """The kind of ``path`` from its name alone (no disk access)."""
    name = PurePath(str(path).replace("\\", "/")).name.lower()
    ext = _ext(name)
    if ext in BUILD_ARTIFACT_EXTENSIONS or in_build_dir(path):
        return BUILD_ARTIFACT
    if ext in MEDIA_EXTENSIONS:
        return MEDIA
    if ext in CODE_EXTENSIONS or (not ext and name in CODE_FILENAMES):
        return CODE
    return PROSE


def is_code(path: PathLike) -> bool:
    return kind_of(path) == CODE


def is_media(path: PathLike) -> bool:
    return kind_of(path) == MEDIA


def is_image(path: PathLike) -> bool:
    return is_media(path) and _ext(path) in IMAGE_EXTENSIONS


def is_build_artifact(path: PathLike) -> bool:
    return kind_of(path) == BUILD_ARTIFACT


def flags_as_code_file(path: PathLike) -> bool:
    """The ``documents.is_code_file`` flag: source plus markup, config and markdown,
    which the Code page lists alongside it."""
    ext = _ext(path)
    return (kind_of(path) == CODE
            or ext in MARKUP_CONFIG_EXTENSIONS
            or ext in MARKDOWN_EXTENSIONS)


def looks_binary(path: PathLike, sample_bytes: int = 8192) -> bool:
    """A NUL byte in the first 8 KB, the test git and grep use for binary files."""
    try:
        with open(Path(path), "rb") as f:
            return b"\x00" in f.read(sample_bytes)
    except OSError:
        return False


def never_read_as_text(path: PathLike) -> bool:
    """Media, build output and binary files: their bytes are not text to store or embed."""
    if kind_of(path) in (MEDIA, BUILD_ARTIFACT):
        return True
    return _ext(path) not in PARSED_DOCUMENT_EXTENSIONS and looks_binary(path)
