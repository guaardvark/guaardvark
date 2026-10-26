"""Plan mode in the REPL: the switch, the approve prompt, and the plan's run.

The session row on the server holds the switch, as it does for the web chat;
the REPL caches it in ``state["plan_mode"]`` for its prompt marker. While it
is on, only the commands below run here; any other line goes to the server,
which researches it and answers with a plan to approve.
"""

from __future__ import annotations

from rich.markup import escape

# REPL commands that look, switch modes or manage the REPL itself, and so run
# as usual in plan mode. Anything else (/edit, /run, /test, /apply, /imagine,
# /remember, ...) is sent to the server as the objective of a plan.
PLAN_MODE_COMMANDS = frozenset({
    "help", "quit", "exit", "new", "abort", "clear", "history", "export", "config", "theme",
    "plan", "agent", "ls", "cd", "pwd", "read", "grep", "diff", "tools", "context", "web",
    "todo", "skills", "suggest",
})


def command_runs_in_plan_mode(line: str) -> bool:
    name = line.lstrip("/").split(maxsplit=1)[0].lower() if line.strip("/ ") else ""
    return name in PLAN_MODE_COMMANDS


def set_plan_mode(client, session_id: str, on: bool) -> bool:
    """Switch plan mode for the session; returns the server's answer."""
    data = client.patch(f"/api/chat-sessions/{session_id}/plan-mode", json={"on": bool(on)})
    return bool(data.get("plan_mode"))


def refresh(state: dict) -> None:
    """Read the session's plan-mode switch (after starting or resuming a chat)."""
    try:
        from llx.client import get_client
        data = get_client(state["server"]).get(f"/api/chat-sessions/{state['session_id']}/mode")
        state["plan_mode"] = bool(data.get("plan_mode"))
    except Exception:
        state["plan_mode"] = False


def toggle(state: dict, console, want: bool | None = None) -> None:
    from llx.client import get_client

    on = (not state.get("plan_mode")) if want is None else want
    try:
        state["plan_mode"] = set_plan_mode(get_client(state["server"]), state["session_id"], on)
    except Exception as e:  # noqa: BLE001 - shown to the person
        console.print(f"[llx.error]Could not switch plan mode: {escape(str(e))}[/llx.error]")
        return
    if state["plan_mode"]:
        console.print("[llx.success]Plan mode ON[/llx.success]")
        console.print("[llx.dim]I'll look into what you ask and show a plan; nothing changes until you "
                      "approve it. /plan or Shift+Tab turns it off.[/llx.dim]")
    else:
        console.print("[llx.dim]Plan mode OFF.[/llx.dim]")


def after_turn(state: dict, console, complete: dict | None) -> None:
    """When the reply was a plan waiting for approval, ask what to do with it."""
    plan = (complete or {}).get("plan") or {}
    if not plan or plan.get("status") != "awaiting_approval" or (complete or {}).get("plan_result"):
        return
    import typer
    from llx.client import get_client

    tools = sorted({t for s in plan.get("steps") or [] for t in s.get("tools") or []})
    console.print()
    console.print("[bold]Approve this plan?[/bold]")
    console.print("  [bold]a[/bold]  approve, asking before each step that changes something")
    console.print(f"  [bold]A[/bold]  approve and allow its actions{' (' + ', '.join(tools) + ')' if tools else ''}")
    console.print("  [bold]k[/bold]  keep planning (type your feedback next)")
    console.print("  [bold]r[/bold]  reject")
    try:
        choice = typer.prompt("Choice", default="k", show_default=True).strip()
    except (KeyboardInterrupt, EOFError, Exception):
        choice = "k"
    client = get_client(state["server"])
    if choice in ("a", "A"):
        mode = "ask" if choice == "a" else "allow_plan_tools"

        def start():
            client.post(f"/api/chat/plans/{plan['id']}/approve", json={"mode": mode, "revision": plan.get("revision")})
            state["plan_mode"] = False

        run_plan(state, console, plan["id"], start)
    elif choice == "r":
        try:
            client.post(f"/api/chat/plans/{plan['id']}/reject", json={})
            console.print("[llx.dim]Plan rejected. Plan mode stays on.[/llx.dim]")
        except Exception as e:  # noqa: BLE001
            console.print(f"[llx.error]{escape(str(e))}[/llx.error]")
    else:
        console.print("[llx.dim]Say what to change; your next message revises the plan.[/llx.dim]")


def run_plan(state: dict, console, plan_id: str, start) -> None:
    """Start a plan run with ``start()`` once listening, follow it to its end
    (answering approval cards here), then print what each step did."""
    from llx.client import get_client
    from llx.streaming import ChatRenderer, LlxStreamer

    server = state["server"]
    session_id = state["session_id"]
    client = get_client(server)
    renderer = ChatRenderer(server_url=server)
    streamer = LlxStreamer(server)
    renderer.start()
    completed = False
    try:
        streamer.stream_chat(session_id, **renderer.stream_callbacks())
        try:
            start()
        except Exception as e:  # noqa: BLE001 - shown to the person
            console.print(f"[llx.error]Could not start the plan: {escape(str(e))}[/llx.error]")
            return
        completed = streamer.wait_for_completion(
            approval_handler=lambda data: renderer.prompt_for_approval(data),
            esc_stops=True,
        )
    except KeyboardInterrupt:
        streamer.hard_abort(session_id, client)
        console.print("[llx.dim]Plan stopped.[/llx.dim]")
    finally:
        renderer.stop()
        streamer.disconnect()
    try:
        plan = client.get(f"/api/chat/plans/{plan_id}").get("plan") or {}
    except Exception:
        plan = {}
    for step in plan.get("steps") or []:
        mark = {"done": "✓", "failed": "✗", "skipped": "↷", "stopped": "■"}.get(step.get("status"), "·")
        console.print(f" {mark} [bold]{step.get('index')}. {escape(step.get('title') or '')}[/bold]")
        if step.get("summary"):
            console.print(f"   [llx.dim]{escape(step['summary'][:300])}[/llx.dim]")
        if step.get("error"):
            console.print(f"   [llx.error]{escape(step['error'])}[/llx.error]")
    if plan.get("status") == "paused":
        console.print("[llx.dim]The plan is paused. Continue it from the web chat, or say what to do next.[/llx.dim]")
    elif not completed and not plan:
        console.print("[llx.error]Lost track of the plan run.[/llx.error]")
