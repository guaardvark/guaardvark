"""Semantic search command."""

import typer

from llx.client import get_client, LlxError
from llx.global_opts import get_global_json, get_global_server
from llx.kb import search_passages
from llx.theme import make_console
from llx import output

console = make_console()


def _snippet(text: str, width: int = 110) -> str:
    """The passage body on one line: the index's "Document: … Section: …" label
    (already in the source column) and line breaks dropped."""
    head, sep, body = text.partition("\n\n")
    if sep and head.startswith(("Document:", "[")):
        text = body
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width].rstrip() + "…"


def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, "--limit", "-n", help="Max results"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Search your indexed documents: the best-matching passages, with their scores."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        passages, trace = search_passages(client, query, limit)

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {"results": passages, "retrieval": trace}})
            return

        if not passages:
            console.print(f"[llx.dim]Nothing indexed matches: {query}[/llx.dim]")
            return

        rows = [{
            "source": p["source"] + (f" p.{p['page']}" if p["page"] else ""),
            "score": f"{p['score']:.3f}" if isinstance(p["score"], (int, float)) else "—",
            "passage": _snippet(p["text"]),
        } for p in passages]
        output.print_table(rows, columns=["source", "score", "passage"], title=f"Search: {query}")
        rerank = (trace or {}).get("rerank") or {}
        if rerank.get("applied"):
            console.print("[llx.dim]Scores come from the reranker: closer to 1 is a better match.[/llx.dim]")
        else:
            why = f" ({rerank['reason']})" if rerank.get("reason") else ""
            console.print(f"[llx.dim]The reranker did not run{why}; these scores only order the results.[/llx.dim]")

    except LlxError as e:
        raise output.fail(e)
