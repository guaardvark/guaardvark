"""Search this checkout's source by meaning or keyword.

One tool name for "search the code", whatever is behind it. With the
zvec-grep plugin connected (plugins/zvec_grep) the query runs hybrid: vector
plus keyword over one local index, and a plain-language question such as
"where is it decided whether a model supports thinking" comes back with the
defining function first. Without it the query degrades to the repository's
regex search, so the tool never advertises more than the machine can do.

The workspace root is never asked of the model: the MCP tool behind this one
requires an absolute root, and in the first trial the model guessed, ran
`pwd`, and burned two turns finding it. It comes from the request's
project_root, else the configured Guaardvark root. Over Guaardvark's own MCP
server it is always the Guaardvark checkout: the client works in a repository
of its own, and this tool never reaches outside the checkout for it.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.backend_http import is_mcp_transport
from backend.utils.display_paths import display_path

logger = logging.getLogger(__name__)

MCP_SERVER = "zvec_grep"
MCP_TOOL = "zvec_grep_search"
DEFAULT_LIMIT = 8
# What the model gets to read per call. zg returns ~1.3 KB per hit with source
# lines; eight hits is enough to answer and small enough to leave room for the
# answer in the context window.
MAX_OUTPUT_CHARS = 12000

_SEMANTIC_UNAVAILABLE = (
    "Semantic code search is unavailable (the zvec_grep plugin is not connected, or has no "
    "index for this checkout), and the query has no code names to search for literally. Ask "
    "with a function, class or file name, or start the zvec_grep plugin from the Plugins page."
)
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def _default_root() -> str:
    from backend.services.guarded_code_service import default_repo_root
    return str(default_repo_root())


def _resolve_root(explicit: Optional[str], agent_context: Optional[Dict[str, Any]],
                  confine: bool = False) -> str:
    """The checkout to search. A relative ``root`` ("frontend/src") narrows the
    project root rather than failing: the model reaches for that when it wants
    to look in one area, and the index is per checkout anyway.

    With ``confine`` (the MCP transport) the base is always the Guaardvark
    checkout, whatever the arguments carry, and a root outside it raises
    ValueError instead of being searched."""
    ctx = {} if confine else (agent_context or {})
    ctx_root = ctx.get("project_root")
    # A request root that is not a folder (an API route the chat page took
    # for a path) falls back to the checkout instead of failing the search.
    if ctx_root and not Path(str(ctx_root)).expanduser().is_dir():
        ctx_root = None
    base = Path(str(ctx_root or _default_root())).expanduser().resolve()
    if not explicit:
        return str(base)
    given = Path(str(explicit)).expanduser()
    if not given.is_absolute():
        given = base / given
    given = given.resolve()
    # zg indexes the checkout as a whole; a sub-directory of it searches the
    # same index, so the call is made with the checkout root.
    try:
        given.relative_to(base)
        return str(base)
    except ValueError:
        if confine:
            raise ValueError(
                f"root '{display_path(explicit)}' is outside this Guaardvark install's own source "
                "checkout, which is all search_codebase searches over MCP. Leave root empty to "
                "search Guaardvark's source; use your own tools for other folders."
            ) from None
        return str(given)


def _checkout_relative(root: str) -> Optional[str]:
    """root relative to the Guaardvark checkout ("." for the checkout itself),
    or None when it lies outside. The checkout is the configured root or the
    one search_code reads; they differ only when GUAARDVARK_ROOT points away
    from the code that is running."""
    from backend.tools.llama_code_tools import PROJECT_ROOT
    path = Path(root).resolve()
    for checkout in (Path(_default_root()).resolve(), PROJECT_ROOT.resolve()):
        try:
            return path.relative_to(checkout).as_posix()
        except ValueError:
            continue
    return None


def _is_checkout(root: str) -> bool:
    """True when root is (inside) the Guaardvark checkout, the only tree the
    regex fallback can search."""
    return _checkout_relative(root) is not None


def _root_label(root: str) -> str:
    """The searched root as the result names it, never as an absolute path:
    "Guaardvark's own source checkout (.)" for the checkout itself."""
    rel = _checkout_relative(root)
    if rel is not None:
        return f"Guaardvark's own source checkout ({rel})"
    return display_path(root)


def _hybrid_search(root: str, query: str, limit: int) -> Optional[str]:
    """Ask the zvec-grep MCP server; None when it is not there or refuses."""
    try:
        from backend.services.mcp_client_service import MCP_ENABLED, get_mcp_service
    except Exception as exc:  # noqa: BLE001 - the fallback covers it
        logger.debug("code search: MCP client unavailable: %s", exc)
        return None
    if not MCP_ENABLED:
        return None
    try:
        service = get_mcp_service()
        result = service.call_tool(
            MCP_SERVER, MCP_TOOL, {"root": root, "query": query, "limit": int(limit)},
            caller="code_search",
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("code search: hybrid call failed, using regex search: %s", exc)
        return None
    return _mcp_text(result)


def _hybrid_search_via_backend(root: str, query: str, limit: int) -> Optional[str]:
    """The MCP server process has no zvec-grep client of its own; the backend
    holds that connection, so the search runs there."""
    from backend.utils.backend_http import BackendError, request_json

    call = {"server": MCP_SERVER, "tool": MCP_TOOL, "arguments": {"root": root, "query": query, "limit": int(limit)}}

    def _execute() -> Optional[Dict[str, Any]]:
        body = request_json("POST", "/api/automation/mcp/execute", payload=call, read_timeout=60).body
        return body if isinstance(body, dict) else None

    try:
        try:
            result = _execute()
        except BackendError as exc:
            if exc.kind != "http":
                raise
            result = None
        if not (result and result.get("success")):
            # The backend connects MCP servers on demand. A search is safe to repeat.
            request_json("POST", "/api/automation/mcp/connect", payload={"server": MCP_SERVER}, read_timeout=60)
            result = _execute()
    except BackendError as exc:
        logger.info("code search: backend hybrid call failed, using regex search: %s", exc)
        return None
    return _mcp_text(result)


def _mcp_text(result: Optional[Dict[str, Any]]) -> Optional[str]:
    """The text of a successful zvec-grep call, or None."""
    if not result or not result.get("success"):
        return None
    payload = result.get("result") or {}
    content = payload.get("content") if isinstance(payload, dict) else None
    text = "\n".join(
        item.get("text", "") for item in (content or []) if isinstance(item, dict) and item.get("type") == "text"
    ).strip()
    if not text or (isinstance(payload, dict) and payload.get("isError")):
        logger.info("code search: hybrid returned an error, using regex search: %s", text[:200])
        return None
    return text


def _symbol_tokens(query: str) -> list[str]:
    """Words in the query that look like code names: snake_case, camelCase or
    dotted. Plain English words would match most of the tree."""
    tokens = []
    for word in _SYMBOL.findall(query):
        if "_" in word or "." in word or re.search(r"[a-z][A-Z]", word):
            tokens.append(word)
    return tokens[:6]


_STOPWORDS = frozenset("""
    a an and are code does file files find for from function functions how in into is it its
    of on or project that the this to what where which who why with
""".split())


def _content_words(query: str) -> list[str]:
    """The query's longer words that are not filler, for a literal search of a
    plain-English question ("where is a blocked address checked")."""
    words = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", query):
        w = word.lower()
        if w not in _STOPWORDS and w not in words:
            words.append(w)
    return words[:6]


def _regex_search(query: str, limit: int = DEFAULT_LIMIT, root: Optional[str] = None) -> tuple[Optional[str], str]:
    """The repository's regex search, over ``root`` (default: the Guaardvark
    checkout), for when semantic search is unavailable. Tries the query as
    written, then its code-like names, then its content words. Lists at most
    ``limit`` hits.

    Returns (hits, searched_for). hits is None when the query has nothing a
    literal search can use.
    """
    from contextlib import nullcontext
    from backend.tools.llama_code_tools import rooted_at, search_code

    with rooted_at(root) if root else nullcontext():
        out = search_code(re.escape(query), max_hits=limit)
        if "No matches" not in out and out.strip():
            return out, query
        symbols, words = _symbol_tokens(query), _content_words(query)
        if symbols:
            out = search_code("|".join(re.escape(t) for t in symbols), max_hits=limit)
            if "No matches" not in out:
                return out, ", ".join(symbols)
        if words:
            # Definitions named after the question's words first: "which function
            # blocks an address" should list def validate_block before every line
            # that mentions an address.
            stems = "|".join(re.escape(w[:-1] if w.endswith("s") and not w.endswith("ss") else w) for w in words)
            definition = rf"^\s*(?:async\s+)?(?:def|class|function|const|let|var|func|fn)\s+\w*(?:{stems})\w*"
            out = search_code(definition, max_hits=limit)
            if "No matches" not in out:
                return out, "definitions named after " + ", ".join(words)
            out = search_code("|".join(re.escape(w) for w in words), max_hits=limit)
            if "No matches" not in out:
                return out, ", ".join(words)
        return None, ""


class SearchCodebaseTool(BaseTool):
    name = "search_codebase"
    read_only = True
    description = (
        "Search this Guaardvark install's own source code by meaning or by symbol. It does not "
        "search the caller's workspace or any other repository. The checkout is already indexed, "
        "so no path or upload is needed: ask 'where is it decided whether a model supports "
        "thinking', 'which function cuts retrieved text', 'callers of think_payload'. The first "
        "line of the result names the checkout searched and how; then come the matching files "
        "with checkout-relative line numbers and the code itself. With the zvec_grep plugin "
        "connected the search is hybrid (meaning plus keyword); without it, a literal search "
        "for the query and for the code names in it. Use it before saying Guaardvark's code is "
        "unavailable and before reading whole files. For an exact name or a regular expression "
        "use search_code; read_code opens a file it finds."
    )
    category = "code"
    observation_chars = MAX_OUTPUT_CHARS
    parameters = {
        "query": ToolParameter(
            name="query", type="string", required=True,
            description="What to find, in plain words or as a symbol name",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False, default=DEFAULT_LIMIT,
            description="How many hits to return, 1 to 25 (default 8)",
        ),
        "root": ToolParameter(
            name="root", type="string", required=False,
            description=(
                "Leave empty. Defaults to Guaardvark's own checkout (in chat, the project folder "
                "the request came from); a folder inside it searches the same whole-checkout "
                "index. Over MCP a root outside the checkout is refused."
            ),
        ),
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(success=False, error="query is required")
        try:
            limit = max(1, min(int(kwargs.get("limit") or DEFAULT_LIMIT), 25))
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        over_mcp = is_mcp_transport(self)
        try:
            root = _resolve_root(kwargs.get("root"), kwargs.get("_agent_context"), confine=over_mcp)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if not Path(root).is_dir():
            return ToolResult(success=False, error=f"root is not a directory: {display_path(root)}")
        label = _root_label(root)
        meta = {"root": root, "limit": limit}

        if over_mcp:
            text = _hybrid_search_via_backend(root, query, limit)
        else:
            text = _hybrid_search(root, query, limit)
        engine = "hybrid"
        if text is not None:
            text = f"[Searched {label}: hybrid meaning and keyword search (zvec_grep)]\n{text}"
        else:
            engine = "regex"
            hits, searched_for = _regex_search(query, limit, None if _is_checkout(root) else root)
            if hits is None:
                return ToolResult(success=False, error=_SEMANTIC_UNAVAILABLE, metadata={"engine": engine, **meta})
            if hits.startswith("ERROR"):
                return ToolResult(success=False, error=hits, metadata={"engine": engine, **meta})
            text = (
                f"[Searched {label}: literal search for {searched_for}, at most {limit} hits listed. "
                "Semantic search is unavailable (the zvec_grep plugin is not connected, or has no "
                f"index for this folder).]\n{hits}"
            )
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS].rsplit("\n", 1)[0] + "\n... [more hits omitted]"
        return ToolResult(success=True, output=text, metadata={"engine": engine, **meta})
