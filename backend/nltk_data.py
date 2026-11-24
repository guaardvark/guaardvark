"""A private NLTK data folder that the app owns.

NLTK 3.10.4 and later refuse to read data from a folder another account could
write to (a group or world write bit on the folder or anything in it). The
sentence tokenizer and stopwords llama-index uses ship inside its package, and
a virtualenv created under umask 0002 (the Ubuntu default for a user account)
is group-writable, so NLTK rejects that copy, llama-index then tries to download
one, and prose chunking falls back to a coarser split: 21 chunks against 115
for this repository's README.

So the app keeps its own copy under the cache root, readable by its owner only,
and points NLTK_DATA at it. The copy is made from the files llama-index already
installed; nothing is fetched. An NLTK_DATA the user set is left alone.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

FOLDER_NAME = "nltk_data"
_STAMP = ".source"

# The folder this process pointed NLTK_DATA at, so a later call with the cache
# root config resolved can move it without mistaking it for the user's choice.
_installed: Optional[str] = None


def _default_cache_root() -> Path:
    """The cache root as backend.config resolves it, without importing config
    (this runs from the package __init__, before .env is read)."""
    if os.environ.get("GUAARDVARK_MODE") == "test":
        return Path(tempfile.gettempdir()) / "guaardvark_test" / "cache"
    root = Path(os.environ.get("GUAARDVARK_ROOT") or Path(__file__).resolve().parents[1])
    cache = Path(os.environ.get("GUAARDVARK_CACHE_DIR", "data/cache"))
    return cache if cache.is_absolute() else root / cache


def _bundled_cache() -> Optional[Path]:
    """llama-index's packaged NLTK data, located without importing the package."""
    try:
        spec = importlib.util.find_spec("llama_index.core")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    bundled = Path(spec.origin).parent / "_static" / "nltk_cache"
    return bundled if bundled.is_dir() else None


def _source_id(bundled: Path) -> str:
    try:
        version = importlib.metadata.version("llama-index-core")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return f"llama-index-core {version}\n{bundled}\n"


def _make_private(top: Path) -> None:
    os.chmod(top, 0o700)
    for current, dirs, files in os.walk(top):
        for name in dirs:
            os.chmod(os.path.join(current, name), 0o700)
        for name in files:
            os.chmod(os.path.join(current, name), 0o600)


def _is_current(target: Path, source_id: str) -> bool:
    try:
        return (target / _STAMP).read_text(encoding="utf-8") == source_id
    except OSError:
        return False


def _build(target: Path, bundled: Path, source_id: str) -> None:
    """Copy the bundled data beside the target, then swap it in, so a process
    reading the folder never sees it half written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{FOLDER_NAME}-", dir=target.parent))
    try:
        for entry in bundled.iterdir():
            if entry.is_dir():
                shutil.copytree(entry, staging / entry.name)
        (staging / _STAMP).write_text(source_id, encoding="utf-8")
        _make_private(staging)
        if target.exists():
            retired = Path(tempfile.mkdtemp(prefix=f".{FOLDER_NAME}-old-", dir=target.parent))
            os.rename(target, retired / FOLDER_NAME)
            os.rename(staging, target)
            shutil.rmtree(retired, ignore_errors=True)
        else:
            os.rename(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def install(cache_dir: Optional[str] = None) -> Optional[str]:
    """Point NLTK_DATA at the app's private copy, creating or refreshing it.

    Returns the folder in use, or None when NLTK_DATA was already set by the
    user or the copy could not be made. Never raises: without the folder the
    app still runs, with the coarser chunking described above.
    """
    global _installed
    if os.environ.get("NLTK_DATA") and os.environ["NLTK_DATA"] != _installed:
        return None
    try:
        bundled = _bundled_cache()
        if bundled is None:
            return None
        target = Path(cache_dir) / FOLDER_NAME if cache_dir else _default_cache_root() / FOLDER_NAME
        source_id = _source_id(bundled)
        if _is_current(target, source_id):
            # Cheap to redo, and NLTK rejects the whole folder if one entry in
            # it has been loosened since.
            _make_private(target)
        else:
            try:
                _build(target, bundled, source_id)
            except OSError:
                # Another process starting at the same moment may be putting
                # the folder in place; give its swap a moment to finish.
                for _ in range(10):
                    if _is_current(target, source_id):
                        break
                    time.sleep(0.1)
                else:
                    raise
        os.environ["NLTK_DATA"] = _installed = str(target)
        return _installed
    except Exception as exc:
        logger.warning("Private NLTK data folder not set up: %s", exc)
        return None
