"""Whether source code goes into the knowledge index.

``on_demand`` (the default): code files are stored, listed and opened, and a
chat reads them in place with the code tools (in its project's folder when it
has one). They are not embedded, so knowledge-base answers come from documents.
``embed``: code is also chunked into the vector index, as before 2026-10.

The setting is ``code_retrieval`` (Settings table, else GUAARDVARK_CODE_RETRIEVAL).
Markup, configuration and markdown are documents either way.
"""
from __future__ import annotations

import logging

from backend.utils import file_kinds

logger = logging.getLogger(__name__)

ON_DEMAND = "on_demand"
EMBED = "embed"
DEFAULT = ON_DEMAND

# add_file_to_index's result for a code file it stored without embedding. Truthy,
# so callers that only test success still move on; callers that set a status
# compare against it and leave the row STORED.
STORED_ON_DEMAND = "stored_on_demand"


def _normalized(value) -> str:
    value = str(value or "").strip().lower().replace("-", "_")
    return value if value in (ON_DEMAND, EMBED) else DEFAULT


def code_retrieval_mode(session=None) -> str:
    """The mode, read from the Settings table through ``session`` (a plain
    SQLAlchemy session, for workers without an app) or through get_setting."""
    if session is not None:
        try:
            from sqlalchemy import text

            row = session.execute(text("SELECT value FROM settings WHERE key = 'code_retrieval'")).fetchone()
            if row and row[0] is not None:
                return _normalized(row[0])
        except Exception as e:  # noqa: BLE001 - an unreadable setting means the default
            logger.debug(f"code_retrieval not read from the database: {e}")
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
    from backend.utils.settings_utils import get_setting

    return _normalized(get_setting("code_retrieval", default=DEFAULT))


def reads_on_demand(file_path, session=None) -> bool:
    """True when ``file_path`` is source code and code is not embedded."""
    return file_kinds.is_code(file_path) and code_retrieval_mode(session) == ON_DEMAND
