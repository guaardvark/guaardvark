"""Bridge between chat uploads and the agent's code-reading tools.

When a user drops a file into chat, it lands in ``data/uploads/`` and gets
a row in the ``documents`` table — but ``read_code`` / ``analyze_code``
only know how to look at ``PROJECT_ROOT/<path>``. So when the LLM helpfully
calls ``read_code("app.py")`` after the user uploads ``app.py``, it whiffs.
This helper closes that gap.
"""

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def find_uploaded_file(filename: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Look up the most recently uploaded Document by exact filename.

    Returns ``(content, on_disk_path)`` for the newest matching Document,
    or ``None`` if no Document exists with that filename. Either field
    may be ``None`` — callers should prefer ``content`` (it's already in
    memory and survives even if the on-disk file was moved/deleted) and
    fall back to reading ``on_disk_path``.

    The lookup matches on basename only — the LLM almost always passes a
    bare filename ("app.py"), not a full path.
    """
    try:
        from backend.models import Document, db
        from backend.config import UPLOAD_DIR

        base = os.path.basename(filename)
        if not base:
            return None

        doc = (
            db.session.query(Document)
            .filter(Document.filename == base)
            .order_by(Document.id.desc())
            .first()
        )
        if not doc:
            return None

        on_disk: Optional[str] = None
        if doc.path:
            candidate = os.path.join(UPLOAD_DIR, doc.path)
            if os.path.isfile(candidate):
                on_disk = candidate

        content = doc.content if doc.content else None
        if content is None and on_disk is None:
            return None

        return (content, on_disk)
    except Exception as e:
        # Never raise out of a tool's fallback path — just log and miss.
        logger.debug(f"Uploaded file lookup failed for {filename!r}: {e}")
        return None
