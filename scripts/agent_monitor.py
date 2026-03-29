#!/usr/bin/env python3
"""
GUAARDVARK AGENT MONITOR
Live transcript of agent control tasks — see what the agent sees, thinks, and does.

Usage:
    python3 scripts/agent_monitor.py "Navigate to the chat page and send a test message"
    python3 scripts/agent_monitor.py --capture "Describe what you see"
    python3 scripts/agent_monitor.py --status
    python3 scripts/agent_monitor.py --tail          # Watch agent log only
    python3 scripts/agent_monitor.py                 # Interactive mode
"""

import sys
import os
import time
import json
import argparse
import threading
import requests

API = os.environ.get("GUAARDVARK_API", "http://localhost:5002")
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
# Tail whichever log file was modified most recently
_LOG_CANDIDATES = [os.path.join(_LOG_DIR, f) for f in ("backend.log", "backend_startup.log")]
LOG_PATH = os.environ.get("GUAARDVARK_LOG",
    max([p for p in _LOG_CANDIDATES if os.path.exists(p)] or _LOG_CANDIDATES[:1],
        key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0))

# ANSI colors
C = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "red":     "\033[91m",
    "green":   "\033[92m",
    "yellow":  "\033[93m",
    "blue":    "\033[94m",
    "magenta": "\033[95m",
    "cyan":    "\033[96m",
    "white":   "\033[97m",
}

# Color map for [AGENT] log phases
PHASE_COLORS = {
    "[SEE]":   "cyan",
    "[THINK]": "yellow",
    "[ACT]":   "magenta",
    "[DONE]":  "green",
}


def c(color, text):
    return f"{C[color]}{text}{C['reset']}"


def banner():
    print()
    print(c("cyan", "=" * 70))
    print(c("cyan", "  GUAARDVARK AGENT MONITOR"))
    print(c("cyan", "  Live transcript of agent vision control"))
    print(c("cyan", "=" * 70))
    print()


def format_agent_log(line):
    """Parse and colorize an [AGENT] log line for the transcript."""
    # Extract the [AGENT] portion from the full log line
    idx = line.find("[AGENT]")
    if idx == -1:
        return None
    agent_part = line[idx:]

    # Determine phase color
    color = "white"
    for phase, phase_color in PHASE_COLORS.items():
        if phase in agent_part:
            color = phase_color
            break

    # Highlight success/fail markers
    display = agent_part
    if "[OK]" in display:
        display = display.replace("[OK]", c("green", "[OK]"))
    if "[FAIL]" in display:
        display = display.replace("[FAIL]", c("red", "[FAIL]"))

    return f"  {c(color, display)}"


class LogTailer:
    """Tail backend log files for [AGENT] entries in background threads."""

    def __init__(self):
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        self.log_paths = [
            os.path.join(log_dir, "backend.log"),
            os.path.join(log_dir, "backend_startup.log"),
        ]
        self._stop = threading.Event()
        self._threads = []
        self._seen = set()  # Deduplicate across files
        self._lock = threading.Lock()

    def start(self):
        """Start tailing all log files in background."""
        for path in self.log_paths:
            if os.path.exists(path):
                t = threading.Thread(target=self._tail, args=(path,), daemon=True)
                t.start()
                self._threads.append(t)

    def stop(self):
        """Stop all tailers."""
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)

    def _tail(self, log_path):
        """Tail one log file, printing [AGENT] lines."""
        try:
            with open(log_path, "r") as f:
                f.seek(0, 2)
                while not self._stop.is_set():
                    line = f.readline()
                    if line:
                        if "[AGENT]" in line:
                            # Deduplicate: use the [AGENT] portion as key
                            key = line[line.find("[AGENT]"):].strip()
                            with self._lock:
                                if key in self._seen:
                                    continue
                                self._seen.add(key)
                            formatted = format_agent_log(line.rstrip())
                            if formatted:
                                print(formatted)
                    else:
                        time.sleep(0.2)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(c("red", f"  [WARN] Log tailer error ({log_path}): {e}"))


def capture(prompt="Describe what is on screen"):
    """Take a screenshot and get vision analysis."""
    print(c("cyan", f"  [CAPTURE] ") + c("dim", prompt))
    try:
        r = requests.post(f"{API}/api/agent-control/capture",
                          json={"prompt": prompt}, timeout=30)
        data = r.json().get("data", r.json())
        desc = data.get("description", "No description")
        model = data.get("model", "unknown")
        ms = data.get("inference_ms", 0)
        print(c("white", f"  [VISION]  ") + f"{desc[:400]}")
        print(c("dim", f"            model={model}  {ms}ms"))
        return desc
    except Exception as e:
        print(c("red", f"  [ERROR]   Capture failed: {e}"))
        return None


def status():
    """Get current agent status."""
    try:
        r = requests.get(f"{API}/api/agent-control/status", timeout=10)
        data = r.json()
        s = data.get("data", data.get("status", data))
        print(c("blue", "  [STATUS]  ") +
              f"active={s.get('active')}  iteration={s.get('iteration')}  "
              f"killed={s.get('killed')}")
        lr = s.get("last_result")
        if lr:
            ok = lr.get("success", False)
            color = "green" if ok else "red"
            print(c(color, f"  [RESULT]  ") +
                  f"success={ok}  reason={lr.get('reason')}  "
                  f"steps={lr.get('steps', lr.get('step_count'))}  "
                  f"time={round(lr.get('time', lr.get('execution_time_seconds', 0)), 1)}s")
        return s
    except Exception as e:
        print(c("red", f"  [ERROR]   Status check failed: {e}"))
        return {}


def execute(task, poll_interval=2):
    """Execute a task and show live transcript with log tailing."""
    print(c("yellow", "  [TASK]    ") + c("bold", task))
    print()

    # Start log tailer BEFORE submitting task
    tailer = LogTailer()
    tailer.start()

    # Submit task
    try:
        r = requests.post(f"{API}/api/agent-control/execute",
                          json={"task": task}, timeout=15)
        data = r.json()
        if not data.get("success"):
            print(c("red", f"  [FAIL]    Could not submit: {data}"))
            tailer.stop()
            return
        print(c("green", "  [SUBMIT]  ") + "Task accepted, tailing log...")
        print()
    except Exception as e:
        print(c("red", f"  [ERROR]   Submit failed: {e}"))
        tailer.stop()
        return

    # Poll for completion while log tailer shows the play-by-play
    start = time.time()

    while True:
        time.sleep(poll_interval)
        elapsed = time.time() - start

        try:
            r = requests.get(f"{API}/api/agent-control/status", timeout=10)
            data = r.json()
            s = data.get("data", data.get("status", data))
            active = s.get("active", False)

            if not active:
                # Give log tailer a moment to catch up
                time.sleep(1)
                tailer.stop()

                lr = s.get("last_result")
                if lr:
                    ok = lr.get("success", False)
                    reason = lr.get("reason", "unknown")
                    steps = lr.get("steps", lr.get("step_count", "?"))
                    t = lr.get("time", lr.get("execution_time_seconds", 0))

                    print()
                    print(c("cyan", "-" * 70))
                    if ok:
                        print(c("green", "  [DONE]    ") +
                              c("bold", f"SUCCESS — {reason}"))
                    else:
                        print(c("red", "  [DONE]    ") +
                              c("bold", f"FAILED — {reason}"))
                    print(c("dim", f"            {steps} steps in {round(t, 1)}s"))
                    print(c("cyan", "-" * 70))

                    # Final screenshot
                    print()
                    capture("Describe what the screen shows now")
                break

            if elapsed > 300:
                tailer.stop()
                print(c("red", "  [TIMEOUT] Monitor timeout (5 min). Task may still be running."))
                break

        except Exception as e:
            print(c("red", f"  [ERROR]   Poll failed: {e}"))

    print()


def tail_only():
    """Just tail the agent log — no tasks, just watching."""
    print(c("yellow", "  Watching agent log for [AGENT] entries..."))
    print(c("dim", f"  Log: {LOG_PATH}"))
    print(c("dim", "  Press Ctrl+C to stop"))
    print()
    tailer = LogTailer()
    tailer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        tailer.stop()
        print()


def main():
    parser = argparse.ArgumentParser(description="Guaardvark Agent Monitor")
    parser.add_argument("task", nargs="?", help="Task to execute")
    parser.add_argument("--capture", "-c", nargs="?", const="Describe what is on screen",
                        help="Just take a screenshot and describe")
    parser.add_argument("--status", "-s", action="store_true",
                        help="Show current agent status")
    parser.add_argument("--tail", "-t", action="store_true",
                        help="Tail the agent log (watch mode)")
    parser.add_argument("--poll", "-p", type=int, default=2,
                        help="Poll interval in seconds (default: 2)")
    args = parser.parse_args()

    banner()

    if args.status:
        status()
    elif args.capture is not None:
        capture(args.capture)
    elif args.tail:
        tail_only()
    elif args.task:
        execute(args.task, poll_interval=args.poll)
    else:
        # Interactive mode with background log tailer
        print(c("yellow", "  Interactive mode — type tasks, 'capture', 'status', 'tail', or 'quit'"))
        print(c("dim", f"  Log tailing: {LOG_PATH}"))
        print()

        # Start persistent log tailer in interactive mode
        tailer = LogTailer()
        tailer.start()

        try:
            while True:
                try:
                    cmd = input(c("cyan", "  agent> ") + C["reset"]).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not cmd:
                    continue
                if cmd.lower() in ("quit", "exit", "q"):
                    break
                elif cmd.lower().startswith("capture"):
                    prompt = cmd[7:].strip() or "Describe what is on screen"
                    capture(prompt)
                elif cmd.lower() == "status":
                    status()
                elif cmd.lower() == "tail":
                    print(c("dim", "  (Log tailer is already running in background)"))
                else:
                    # Stop tailer, execute (which runs its own tailer), then restart
                    tailer.stop()
                    execute(cmd, poll_interval=args.poll)
                    tailer = LogTailer()
                    tailer.start()
                print()
        finally:
            tailer.stop()


if __name__ == "__main__":
    main()
