"""Index profiles: one corpus, several derived projections.

The `Document` registry is the single source of truth for what is in the corpus.
An index is a *projection* of it under a named profile — a chunking strategy, an
embedding model, and retrieval defaults. Profiles are not peers that mirror each
other: deletion happens once, at the registry, and projections are rebuilt from
it. Mirroring two indexes would mean a delete that has to succeed in both or
neither, across two stores with no shared transaction, and a partial failure
leaves them silently disagreeing about what exists.

Two consumers want different things from the same corpus. A small local model
does better with fewer, larger, self-contained passages, because it often will
not make a follow-up call. An MCP client does better with more, finer-grained
passages and will happily chain. Most of that difference is query-time and free;
only the chunking strategy and the embedding model are baked in at index time,
and only those justify a second physical projection.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SETTING_KEY = "index_profiles"

# Retrieval defaults live here rather than in the profile's index, because they
# cost nothing to change and can be tuned without a rebuild.
@dataclass
class IndexProfile:
    name: str
    description: str = ""
    active: bool = True
    # Baked in at index time -- changing either requires a rebuild.
    embedding_model: Optional[str] = None      # None = whatever is globally active
    chunk_strategy: str = "auto"
    # Free at query time.
    top_k: int = 5
    context_window_chunks: int = 3
    rerank: bool = True
    chunk_chars: int = 600
    # Recorded at build time, never set by hand: pgvector bakes the width into
    # the column, so a profile that does not know its dimension cannot be
    # validated against its own table.
    embed_dim: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "IndexProfile":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


# Shipped defaults. `default` reproduces current behaviour exactly, so an
# installation that never opens Settings is unaffected by this feature existing.
BUILTIN_PROFILES: List[IndexProfile] = [
    IndexProfile(
        name="default",
        description="Balanced retrieval. Matches behaviour before profiles existed.",
        active=True,
    ),
    IndexProfile(
        name="local",
        description="Tuned for local Ollama models: fewer, larger, self-contained passages.",
        active=False,
        top_k=4,
        context_window_chunks=3,
        chunk_chars=900,
        rerank=True,
    ),
    IndexProfile(
        name="mcp",
        description="Tuned for MCP clients: more, finer-grained passages to chain over.",
        active=False,
        top_k=12,
        context_window_chunks=8,
        chunk_chars=400,
        rerank=True,
    ),
]


def _default_payload() -> Dict[str, Any]:
    return {"profiles": [p.to_dict() for p in BUILTIN_PROFILES]}


def load_profiles() -> List[IndexProfile]:
    """Read profiles from Settings, falling back to the built-ins.

    Never raises: retrieval must keep working when the DB is unreachable, which
    is the normal state inside the MCP subprocess.
    """
    try:
        from backend.models import Setting, db
        row = db.session.get(Setting, SETTING_KEY)
        if row and row.value:
            payload = json.loads(row.value)
            profiles = [IndexProfile.from_dict(p) for p in payload.get("profiles", [])]
            if profiles:
                return profiles
    except Exception as e:
        logger.debug("index_profiles: falling back to built-ins (%s)", e)
    return list(BUILTIN_PROFILES)


def save_profiles(profiles: List[IndexProfile]) -> None:
    from backend.models import Setting, db
    payload = json.dumps({"profiles": [p.to_dict() for p in profiles]})
    row = db.session.get(Setting, SETTING_KEY)
    if row:
        row.value = payload
    else:
        db.session.add(Setting(key=SETTING_KEY, value=payload))
    db.session.commit()


def get_profile(name: str) -> Optional[IndexProfile]:
    for p in load_profiles():
        if p.name == name:
            return p
    return None


def active_profiles() -> List[IndexProfile]:
    """Active profiles, never empty — an empty set would silently disable retrieval."""
    active = [p for p in load_profiles() if p.active]
    if active:
        return active
    logger.warning("index_profiles: no profile is active; using 'default'")
    fallback = get_profile("default") or BUILTIN_PROFILES[0]
    return [fallback]


def primary_profile() -> IndexProfile:
    """The profile a caller gets when it does not name one."""
    return active_profiles()[0]


def resolve_retrieval_params(profile_name: Optional[str] = None) -> Dict[str, Any]:
    """Query-time knobs for a profile. These need no rebuild to change."""
    p = (get_profile(profile_name) if profile_name else None) or primary_profile()
    return {
        "profile": p.name,
        "top_k": p.top_k,
        "context_window_chunks": p.context_window_chunks,
        "rerank": p.rerank,
        "chunk_chars": p.chunk_chars,
    }


def projection_key(profile_name: Optional[str], project_id=None) -> str:
    """Scope key identifying one projection: profile + project.

    The embedding dimension is appended by the storage layer, so a model change
    lands in a new table rather than contaminating an existing one.
    """
    prof = profile_name or primary_profile().name
    scope = str(project_id) if project_id else "global"
    return f"{prof}_{scope}" if prof != "default" else scope


def set_active(names: List[str]) -> List[IndexProfile]:
    """Activate exactly the named profiles. Unknown names are ignored."""
    profiles = load_profiles()
    wanted = set(names or [])
    for p in profiles:
        p.active = p.name in wanted
    if not any(p.active for p in profiles):
        for p in profiles:
            if p.name == "default":
                p.active = True
    save_profiles(profiles)
    return profiles
