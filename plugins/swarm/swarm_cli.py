#!/usr/bin/env python3
"""
Swarm CLI — launch and manage agent swarms from the command line.

Usage:
    python swarm_cli.py launch <plan.md> [--flight-mode] [--max-agents N] [--auto-merge] [--dry-run]
    python swarm_cli.py status [swarm_id]
    python swarm_cli.py logs <task_id>
    python swarm_cli.py cancel <swarm_id>
    python swarm_cli.py merge <swarm_id>
    python swarm_cli.py cleanup [swarm_id] [--all]
    python swarm_cli.py templates
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# make sure the plugin package is importable
sys.path.insert(0, str(Path(__file__).parent))

from service.backend_url import backend_api_url
from service.config import SwarmConfig, load_config
from service.models import SwarmStatus, TimelineEvent, generate_swarm_id
from service.orchestrator import SwarmOrchestrator
from service.plan_parser import parse_plan, predict_conflicts
from service.worktree_manager import WorktreeManager


# ─── ANSI colors for terminal output ─────────────────────────────────
# because life's too short for monochrome terminals

class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        """Turn off colors (for piped output)."""
        for attr in ("BOLD", "DIM", "GREEN", "YELLOW", "RED", "CYAN", "MAGENTA", "RESET"):
            setattr(cls, attr, "")


# disable colors if not a TTY
if not sys.stdout.isatty():
    C.disable()


# ─── CLI commands ─────────────────────────────────────────────────────

def cmd_launch(args: argparse.Namespace, config: SwarmConfig) -> None:
    """Launch a swarm from a plan file."""
    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"{C.RED}Plan file not found: {plan_path}{C.RESET}")
        sys.exit(1)

    repo_path = Path(args.repo) if args.repo else Path.cwd()

    # override config from CLI flags
    flight_mode = args.flight_mode or config.flight_mode
    max_agents = args.max_agents or config.max_concurrent_agents
    auto_merge = args.auto_merge or config.auto_merge

    print(f"{C.BOLD}Swarm Launch{C.RESET}")
    print(f"  Plan: {plan_path}")
    print(f"  Repo: {repo_path}")
    print(f"  Mode: {'FLIGHT MODE (offline)' if flight_mode else 'online'}")
    print(f"  Max agents: {max_agents}")
    print(f"  Auto-merge: {auto_merge}")
    print()

    if args.dry_run:
        _dry_run(plan_path, config)
        return

    # set up the orchestrator
    orch = SwarmOrchestrator(repo_path, config)

    # wire up live event printing
    orch.on_event(_print_event)

    print(f"{C.CYAN}Launching swarm...{C.RESET}")
    print()

    try:
        result = orch.launch(
            plan_path,
            flight_mode=flight_mode,
            max_agents=max_agents,
            auto_merge=auto_merge,
        )
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Interrupted — cancelling swarm...{C.RESET}")
        orch.cancel()
        return
    except Exception as e:
        print(f"{C.RED}Swarm failed: {e}{C.RESET}")
        sys.exit(1)

    print()
    print(f"{C.BOLD}{'=' * 60}{C.RESET}")
    print(result.summary())
    print(f"{C.BOLD}{'=' * 60}{C.RESET}")

    # show merge status if applicable
    if result.merge_results:
        print()
        print(f"{C.BOLD}Merge Results:{C.RESET}")
        for task_id, mr in result.merge_results.items():
            if mr.success:
                print(f"  {C.GREEN}[merged]{C.RESET} {task_id}")
            else:
                print(f"  {C.RED}[conflict]{C.RESET} {task_id}: {', '.join(mr.conflict_files)}")

    # show any tasks needing attention
    needs_review = [t for t in result.tasks if t.status == SwarmStatus.NEEDS_REVIEW]
    if needs_review:
        print()
        print(f"{C.YELLOW}Tasks needing review:{C.RESET}")
        for t in needs_review:
            print(f"  {t.id}: {t.error or 'merge conflict'}")
            print(f"    Branch: {t.branch_name}")


def cmd_status(args: argparse.Namespace, config: SwarmConfig) -> None:
    """Show status of a swarm."""
    repo_path = Path(args.repo) if args.repo else Path.cwd()
    swarm_base = repo_path / config.worktree_base

    if args.swarm_id:
        _show_swarm_status(swarm_base / args.swarm_id)
    else:
        # list all swarms
        if not swarm_base.exists():
            print("No swarms found.")
            return

        swarm_dirs = sorted(
            [d for d in swarm_base.iterdir() if d.is_dir() and (d / "manifest.json").exists()],
            key=lambda d: d.name,
            reverse=True,
        )

        if not swarm_dirs:
            print("No swarms found.")
            return

        print(f"{C.BOLD}Swarms:{C.RESET}")
        for d in swarm_dirs[:10]:  # show last 10
            result_file = d / "result.json"
            if result_file.exists():
                with open(result_file) as f:
                    data = json.load(f)
                summary = data.get("summary", "")
                print(f"  {C.CYAN}{d.name}{C.RESET}")
                for line in summary.split("\n"):
                    print(f"    {line}")
            else:
                manifest_file = d / "manifest.json"
                if manifest_file.exists():
                    with open(manifest_file) as f:
                        manifest = json.load(f)
                    wt_count = len(manifest.get("worktrees", {}))
                    print(f"  {C.YELLOW}{d.name}{C.RESET} (in progress, {wt_count} worktrees)")
            print()


def _manifests(repo_path: Path, config: SwarmConfig):
    """(swarm id, manifest) for every swarm with worktrees under ``repo_path``."""
    base = repo_path / config.worktree_base
    if not base.is_dir():
        return
    for folder in sorted(base.iterdir()):
        manifest_file = folder / "manifest.json"
        if manifest_file.exists():
            try:
                yield folder.name, json.loads(manifest_file.read_text())
            except (OSError, ValueError):
                continue


def cmd_logs(args: argparse.Namespace, config: SwarmConfig) -> None:
    """Show the log an agent wrote for a task (.swarm-agent.log in its worktree)."""
    task_id = args.task_id
    repo_path = Path(getattr(args, "repo", None) or Path.cwd())
    for swarm_id, manifest in _manifests(repo_path, config):
        worktree = (manifest.get("worktrees") or {}).get(task_id)
        if not worktree:
            continue
        log_file = Path(worktree["worktree_path"]) / ".swarm-agent.log"
        if not log_file.exists():
            print(f"{C.YELLOW}Task '{task_id}' ({swarm_id}) has written no log yet{C.RESET}")
            return
        lines = log_file.read_text(errors="replace").splitlines()
        print("\n".join(lines[-args.lines:]))
        return
    print(f"{C.RED}No worktree found for task '{task_id}' under {repo_path / config.worktree_base}{C.RESET}")


def _service_post(repo_path: Path, path: str, body: dict):
    """POST to the swarm service on this machine. Returns (status, data), or
    (None, reason) when it could not be reached."""
    import urllib.error
    import urllib.request

    secret = os.environ.get("SWARM_INTERNAL_SECRET", "").strip()
    if not secret:
        root = Path(os.environ.get("GUAARDVARK_ROOT") or Path(__file__).resolve().parents[2])
        try:
            secret = (root / "data" / ".swarm_internal_secret").read_text().strip()
        except OSError:
            secret = ""
    request = urllib.request.Request(
        f"http://127.0.0.1:8210{path}", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Swarm-Internal-Token": secret},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:300]}
    except (urllib.error.URLError, OSError) as e:
        return None, str(e)


def cmd_cancel(args: argparse.Namespace, config: SwarmConfig) -> None:
    """Cancel a swarm the swarm service is running."""
    repo_path = Path(args.repo) if args.repo else Path.cwd()
    if not args.swarm_id:
        print(f"{C.RED}Name the swarm to cancel (see `status`){C.RESET}")
        sys.exit(1)
    status, data = _service_post(repo_path, "/swarm/cancel", {"swarm_id": args.swarm_id})
    if status is None:
        print(f"{C.RED}The swarm service was not reached ({data}); nothing was cancelled.{C.RESET}")
        print("  A swarm started with `launch` in a terminal is stopped with Ctrl+C in that terminal.")
        sys.exit(1)
    if status >= 400:
        print(f"{C.RED}Not cancelled ({status}): {data.get('error') or data.get('detail') or data}{C.RESET}")
        sys.exit(1)
    print(f"{C.GREEN}Cancelled swarm {args.swarm_id}{C.RESET}")


def cmd_merge(args: argparse.Namespace, config: SwarmConfig) -> None:
    """Trigger merge for a completed swarm."""
    repo_path = Path(args.repo) if args.repo else Path.cwd()
    result_path = repo_path / config.worktree_base / args.swarm_id / "result.json"

    if not result_path.exists():
        print(f"{C.RED}No result found for swarm {args.swarm_id}{C.RESET}")
        sys.exit(1)

    with open(result_path) as f:
        data = json.load(f)

    from service.merge_manager import MergeManager
    from service.models import SwarmTask

    # reconstruct tasks from saved result
    tasks = []
    for td in data.get("tasks", []):
        task = SwarmTask(
            id=td["id"],
            title=td["title"],
            description=td.get("description", ""),
            branch_name=td.get("branch_name"),
            worktree_path=td.get("worktree_path"),
            status=SwarmStatus(td.get("status", "done")),
        )
        tasks.append(task)

    # The branch the swarm started from is in its manifest; "main" only when
    # the manifest is gone.
    base_branch = data.get("base_branch")
    if not base_branch:
        try:
            manifest = json.loads((repo_path / config.worktree_base / args.swarm_id / "manifest.json").read_text())
            base_branch = manifest.get("base_branch")
        except (OSError, ValueError):
            base_branch = None
    base_branch = base_branch or "main"
    
    mgr = MergeManager(
        repo_path, 
        base_branch,
        enable_merger_agent=config.enable_merger_agent,
        backend_url=backend_api_url()
    )

    merge_queue = mgr.merge_queue(tasks)
    print(f"{C.BOLD}Merging {len(merge_queue)} branches:{C.RESET}")

    for task in merge_queue:
        print(f"  Merging {task.branch_name}...", end=" ")
        result = mgr.attempt_merge(task, run_tests=config.run_tests_before_merge, test_command=config.test_command)
        if result.success:
            print(f"{C.GREEN}OK{C.RESET}")
        else:
            print(f"{C.RED}CONFLICT{C.RESET} ({', '.join(result.conflict_files)})")


def cmd_cleanup(args: argparse.Namespace, config: SwarmConfig) -> None:
    """Clean up worktrees and branches."""
    repo_path = Path(args.repo) if args.repo else Path.cwd()

    if args.all:
        swarm_base = repo_path / config.worktree_base
        if swarm_base.exists():
            import shutil
            # clean up git worktree references first
            import subprocess
            subprocess.run(["git", "-C", str(repo_path), "worktree", "prune"], capture_output=True)
            shutil.rmtree(swarm_base)
            print(f"{C.GREEN}Cleaned up all swarm worktrees{C.RESET}")
        else:
            print("Nothing to clean up.")
        return

    if not args.swarm_id:
        print(f"{C.RED}Specify a swarm_id or use --all{C.RESET}")
        sys.exit(1)

    mgr = WorktreeManager.load_existing(repo_path, args.swarm_id, config.worktree_base)
    if not mgr:
        print(f"{C.RED}No swarm found: {args.swarm_id}{C.RESET}")
        sys.exit(1)

    count = mgr.cleanup_all(delete_branches=True)
    print(f"{C.GREEN}Cleaned up {count} worktrees for {args.swarm_id}{C.RESET}")
    if mgr.kept_branches:
        print(f"{C.YELLOW}Kept {len(mgr.kept_branches)} branch(es) whose work was never merged:{C.RESET}")
        for branch in mgr.kept_branches:
            print(f"  {branch}")


def cmd_templates(args: argparse.Namespace, config: SwarmConfig) -> None:
    """List available swarm templates."""
    template_dir = Path(__file__).parent / "templates"

    if not template_dir.exists():
        print("No templates directory found.")
        return

    templates = sorted(template_dir.glob("*.md"))
    if not templates:
        print("No templates found.")
        return

    print(f"{C.BOLD}Available Swarm Templates:{C.RESET}")
    print()

    for t in templates:
        # read first line (title) and second paragraph (description)
        lines = t.read_text().split("\n")
        title = lines[0].lstrip("# ").strip() if lines else t.stem
        desc = ""
        for line in lines[1:]:
            line = line.strip()
            if line and not line.startswith("#"):
                desc = line
                break

        print(f"  {C.CYAN}{t.name}{C.RESET}")
        print(f"    {title}")
        if desc:
            print(f"    {C.DIM}{desc}{C.RESET}")
        print()

    print(f"Launch with: {C.BOLD}python swarm_cli.py launch templates/<name>.md{C.RESET}")


# ─── Helpers ──────────────────────────────────────────────────────────

def _dry_run(plan_path: Path, config: SwarmConfig) -> None:
    """Parse a plan and show what would happen without actually launching."""
    tasks = parse_plan(plan_path)

    print(f"{C.BOLD}Dry Run — {len(tasks)} tasks parsed:{C.RESET}")
    print()

    for i, task in enumerate(tasks, 1):
        status_color = C.CYAN
        print(f"  {status_color}[{i}]{C.RESET} {C.BOLD}{task.title}{C.RESET} ({task.id})")
        if task.file_scope:
            print(f"      Files: {', '.join(task.file_scope)}")
        if task.dependencies:
            print(f"      Deps:  {', '.join(task.dependencies)}")
        if task.preferred_backend:
            print(f"      Backend: {task.preferred_backend}")
        print()

    # conflict check
    warnings = predict_conflicts(tasks)
    if warnings:
        print(f"{C.YELLOW}Potential Conflicts:{C.RESET}")
        for w in warnings:
            print(f"  {w.task_a_id} <-> {w.task_b_id}")
            print(f"    Files: {', '.join(w.overlapping_files)}")
            print(f"    Recommendation: {w.recommendation}")
            print()
    else:
        print(f"{C.GREEN}No file conflicts detected — all tasks can run in parallel{C.RESET}")


def _print_event(event: TimelineEvent) -> None:
    """Print a timeline event to the terminal. Wired as the orchestrator callback."""
    ts = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
    etype = event.event_type
    task = event.task_id

    color = C.DIM
    icon = " "

    if etype == "task_spawned":
        color = C.CYAN
        icon = ">"
        backend = event.data.get("backend", "?")
        print(f"  {C.DIM}{ts}{C.RESET} {color}{icon} {task}{C.RESET} spawned on {backend}")
    elif etype == "task_completed":
        color = C.GREEN
        icon = "+"
        elapsed = event.data.get("elapsed", "?")
        cost = event.data.get("cost_usd", 0)
        cost_str = f" (${cost:.2f})" if cost > 0 else ""
        print(f"  {C.DIM}{ts}{C.RESET} {color}{icon} {task}{C.RESET} completed in {elapsed}{cost_str}")
    elif etype == "task_failed":
        color = C.RED
        icon = "x"
        error = event.data.get("error", "unknown error")
        print(f"  {C.DIM}{ts}{C.RESET} {color}{icon} {task}{C.RESET} failed: {error}")
    elif etype == "merge_succeeded":
        color = C.GREEN
        icon = "M"
        print(f"  {C.DIM}{ts}{C.RESET} {color}{icon} {task}{C.RESET} merged")
    elif etype == "merge_failed":
        color = C.RED
        icon = "!"
        conflicts = event.data.get("conflict_files", [])
        print(f"  {C.DIM}{ts}{C.RESET} {color}{icon} {task}{C.RESET} merge conflict: {', '.join(conflicts)}")
    elif etype == "swarm_started":
        count = event.data.get("task_count", 0)
        fm = " [FLIGHT MODE]" if event.data.get("flight_mode") else ""
        print(f"  {C.DIM}{ts}{C.RESET} {C.BOLD}Swarm started{fm}: {count} tasks{C.RESET}")
    elif etype == "swarm_completed":
        print(f"  {C.DIM}{ts}{C.RESET} {C.BOLD}Swarm completed{C.RESET}")


def _show_swarm_status(swarm_dir: Path) -> None:
    """Show detailed status for one swarm."""
    result_file = swarm_dir / "result.json"
    if not result_file.exists():
        print(f"{C.YELLOW}Swarm in progress (no result file yet){C.RESET}")
        manifest_file = swarm_dir / "manifest.json"
        if manifest_file.exists():
            with open(manifest_file) as f:
                manifest = json.load(f)
            print(f"  Worktrees: {len(manifest.get('worktrees', {}))}")
        return

    with open(result_file) as f:
        data = json.load(f)

    print(f"{C.BOLD}Swarm: {data.get('swarm_id', '?')}{C.RESET}")
    print(f"  Plan: {data.get('plan_path', '?')}")
    print()
    print(data.get("summary", "(no summary)"))
    print()

    tasks = data.get("tasks", [])
    if tasks:
        print(f"{C.BOLD}Tasks:{C.RESET}")
        for t in tasks:
            status = t.get("status", "?")
            color = {
                "done": C.GREEN, "merged": C.GREEN,
                "failed": C.RED, "needs_review": C.YELLOW,
                "timed_out": C.RED, "no_changes": C.YELLOW,
                "running": C.CYAN, "pending": C.DIM,
            }.get(status, C.RESET)

            elapsed = t.get("elapsed", "-")
            cost = t.get("estimated_cost_usd", 0)
            cost_str = f" ${cost:.2f}" if cost > 0 else ""

            print(f"  {color}[{status:>13}]{C.RESET} {t['title']} ({elapsed}{cost_str})")


# ─── Argument parser ──────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swarm",
        description="Launch and manage swarms of AI coding agents",
    )
    parser.add_argument("--config", type=str, help="Path to config.yaml")
    parser.add_argument("--repo", type=str, help="Target repo path (default: cwd)")

    subs = parser.add_subparsers(dest="command", required=True)

    # launch
    launch_p = subs.add_parser("launch", help="Launch a swarm from a plan file")
    launch_p.add_argument("plan", help="Path to the plan markdown file")
    launch_p.add_argument("--flight-mode", action="store_true", help="Use only offline backends")
    launch_p.add_argument("--max-agents", type=int, help="Max concurrent agents")
    launch_p.add_argument("--auto-merge", action="store_true", help="Auto-merge completed branches")
    launch_p.add_argument("--dry-run", action="store_true", help="Parse plan only, don't launch")

    # status
    status_p = subs.add_parser("status", help="Show swarm status")
    status_p.add_argument("swarm_id", nargs="?", help="Specific swarm ID")

    # logs
    logs_p = subs.add_parser("logs", help="Show agent logs")
    logs_p.add_argument("task_id", help="Task ID to show logs for")
    logs_p.add_argument("--lines", type=int, default=50, help="Number of log lines")

    # cancel
    cancel_p = subs.add_parser("cancel", help="Cancel a running swarm")
    cancel_p.add_argument("swarm_id", nargs="?", help="Swarm ID to cancel (or all)")

    # merge
    merge_p = subs.add_parser("merge", help="Merge completed swarm branches")
    merge_p.add_argument("swarm_id", help="Swarm ID to merge")

    # cleanup
    cleanup_p = subs.add_parser("cleanup", help="Remove worktrees and branches")
    cleanup_p.add_argument("swarm_id", nargs="?", help="Swarm ID to clean up")
    cleanup_p.add_argument("--all", action="store_true", help="Clean up ALL swarm worktrees")

    # templates
    subs.add_parser("templates", help="List available swarm templates")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config(args.config if hasattr(args, "config") and args.config else None)

    commands = {
        "launch": cmd_launch,
        "status": cmd_status,
        "logs": cmd_logs,
        "cancel": cmd_cancel,
        "merge": cmd_merge,
        "cleanup": cmd_cleanup,
        "templates": cmd_templates,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args, config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
