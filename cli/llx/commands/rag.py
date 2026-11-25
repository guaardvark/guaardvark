"""RAG inspection — index stats, entity exploration, evaluation, and debug tracing."""

import typer
from rich.table import Table
from rich.tree import Tree

from llx.client import get_client, LlxError
from llx.global_opts import get_global_json, get_global_server
from llx.kb import list_documents, search_passages
from llx.theme import make_console, make_panel, ICON_SUCCESS, ICON_WARNING
from llx import output

console = make_console()
rag_app = typer.Typer(help="RAG index inspection and evaluation", no_args_is_help=True)


@rag_app.command("status")
def rag_status(
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show RAG index statistics — document counts, index health, storage size."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        listing, meta = list_documents(client)
        try:
            info = client.get("/api/meta/index-info")
        except LlxError:
            info = {}
        docs = []
        for line in listing.splitlines()[1:]:
            name, sep, rest = line.strip().partition(" — ")
            if sep and "passages" in rest:
                docs.append({"document": name, "passages": rest.split(" passages")[0]})
        status_data = {
            "documents": meta.get("total"),
            "passages_listed": sum(int(d["passages"]) for d in docs if d["passages"].isdigit()),
            "embedding_model": info.get("embedding_model"),
            "documents_listed": docs,
        }

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": status_data})
            return

        total = status_data["documents"]
        lines = [
            f"[llx.kv.key]Documents indexed:[/llx.kv.key] {total if total is not None else '?'}",
            f"[llx.kv.key]Passages:[/llx.kv.key]          {status_data['passages_listed']}"
            + (" (first 200 documents)" if (total or 0) > len(docs) else ""),
            f"[llx.kv.key]Embedding model:[/llx.kv.key]   [llx.accent]{status_data['embedding_model'] or '?'}[/llx.accent]",
        ]
        console.print(make_panel("\n".join(lines), title="RAG Index"))
        if docs:
            output.print_table(docs[:10], columns=["document", "passages"],
                               title="Largest documents" if len(docs) > 10 else "Documents")

    except LlxError as e:
        raise output.fail(e)


@rag_app.command("query")
def rag_query(
    query: str = typer.Argument(help="Search query to test against the RAG index"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results to retrieve"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Test a RAG query and see retrieved chunks with relevance scores."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        results, trace = search_passages(client, query, top_k)

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {"results": results, "retrieval": trace}})
            return

        if not results:
            console.print(f"[llx.dim]No results for: {query}[/llx.dim]")
            return

        console.print(f"[llx.accent]Results for:[/llx.accent] [bold]{query}[/bold]\n")

        for i, r in enumerate(results, 1):
            score = r["score"] if isinstance(r["score"], (int, float)) else 0.0
            source = r["source"] + (f" p.{r['page']}" if r["page"] else "")
            text = r["text"][:200]

            score_color = "llx.success" if score > 0.7 else ("llx.warning" if score > 0.4 else "llx.error")
            console.print(f"  [bold]{i}.[/bold] [{score_color}]{score:.3f}[/{score_color}]  [llx.accent]{source}[/llx.accent]")
            console.print(f"     [llx.dim]{text}{'...' if len(r['text']) > 200 else ''}[/llx.dim]")
            console.print()

    except LlxError as e:
        raise output.fail(e)


@rag_app.command("entities")
def rag_entities(
    limit: int = typer.Option(25, "--limit", "-l", help="Max entities to show"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Not available: Guaardvark does not build an entity graph from your documents."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    message = ("Not available: Guaardvark does not build an entity graph from your documents, so "
               "there is nothing to list. `guaardvark rag status` shows what is indexed.")
    if json_out or output.is_pipe():
        output.print_json({"status": "success", "data": {"available": False, "entities": [], "message": message}})
        return
    console.print(f"[llx.dim]{message}[/llx.dim]")


def _score(value) -> str:
    return f"{value:.3f}" if isinstance(value, (int, float)) else "—"


def _autoresearch_lines(st: dict) -> list:
    """Panel lines for `rag eval`, from GET /api/autoresearch/status.

    Scores are the eval judge's 1-5 composite; health checks (pytest
    snapshots) are not experiments and "promoted" counts configs put live.
    """
    running = bool(st.get("running"))
    auto = bool(st.get("auto_enabled"))
    style = "llx.success" if running else "llx.dim"
    lines = [f"[{style}]{ICON_SUCCESS if running else '  '} "
             f"{'Running' if running else 'Idle'}[/{style}] · nightly auto-start "
             f"{'on' if auto else 'off'}"]

    baseline = st.get("baseline_score")
    if baseline:
        when = st.get("baseline_measured_at")
        lines.append(f"[llx.kv.key]Baseline:[/llx.kv.key]       {_score(baseline)} (1-5)"
                     + (f", measured {when}" if when else ", measurement time not recorded"))
    else:
        lines.append("[llx.kv.key]Baseline:[/llx.kv.key]       not measured")

    health = st.get("total_health_checks") or 0
    lines.append(f"[llx.kv.key]Experiments:[/llx.kv.key]    {st.get('total_experiments', 0)} · "
                 f"{st.get('total_improvements', 0)} promoted"
                 + (f" · {health} health checks not counted" if health else ""))

    pairs = st.get("eval_pairs") or {}
    if pairs:
        text = f"{pairs.get('active', 0)} active"
        if pairs.get("not_indexed"):
            text = f"[llx.warning]{text}, {pairs['not_indexed']} from documents not indexed[/llx.warning]"
        lines.append(f"[llx.kv.key]Eval pairs:[/llx.kv.key]     {text}")

    judge = st.get("judge") or {}
    if judge:
        configured = judge.get("configured") or "not set"
        if judge.get("independent") is False:
            lines.append(f"[llx.kv.key]Judge:[/llx.kv.key]          [llx.warning]{ICON_WARNING} {configured}: "
                         f"{judge.get('answer_model') or 'the chat model'} grades its own answers; "
                         "nightly runs refuse[/llx.warning]")
        else:
            lines.append(f"[llx.kv.key]Judge:[/llx.kv.key]          {configured}")

    run = st.get("last_run") or {}
    if run:
        if run.get("status") == "failed_precondition":
            outcome = f"[llx.warning]not run: {run.get('halt_reason') or 'no reason recorded'}[/llx.warning]"
        else:
            outcome = (f"{run.get('status')}, baseline {_score(run.get('baseline_score'))} → latest "
                       f"{_score(run.get('latest_score'))} (best {_score(run.get('best_tried_score'))})")
        lines.append(f"[llx.kv.key]Last run:[/llx.kv.key]       {run.get('run_tag')} — {outcome}")
    else:
        lines.append("[llx.kv.key]Last run:[/llx.kv.key]       never")
    return lines


@rag_app.command("eval")
def rag_eval(
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show RAG autoresearch evaluation results and optimization status."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/autoresearch/status")
        status_data = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": status_data})
            return

        console.print(make_panel("\n".join(_autoresearch_lines(status_data)),
                                 title="RAG Autoresearch"))

    except LlxError as e:
        raise output.fail(e)
