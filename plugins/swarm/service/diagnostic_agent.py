"""
Diagnostic Agent — specialized agent for analyzing and fixing task failures.

When an agent fails a task (crashes or exhausts retries), the Diagnostic
Agent is spawned in the same worktree to read the logs, understand the
failure, and attempt a fix.

Implementation note: this agent runs `claude` as a subprocess with the
worktree as its working directory so the LLM's file-edit tools operate on
the failing task's branch. Earlier versions called the main backend's
async chat endpoint, which (a) executed in the backend's CWD rather than
the worktree and (b) returned a request_id ack synchronously while the
real work streamed over Socket.IO — so the agent always claimed success
without verifying anything. We now verify by checking that HEAD advanced
in the worktree before reporting success back to the orchestrator.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("swarm.diagnostic")

DIAGNOSTIC_TIMEOUT_SECONDS = 600


class DiagnosticAgent:
    """
    Analyzes agent logs and attempts to fix the codebase so the task
    can be completed successfully.
    """

    def __init__(self, backend_url: str, claude_command: str = "claude"):
        # backend_url is kept for signature compatibility with the orchestrator
        # but is no longer used — diagnosis runs locally in the worktree.
        self.backend_url = backend_url
        self.claude_command = claude_command

    def run_diagnosis(
        self,
        worktree_path: str,
        task_title: str,
        task_description: str,
        logs: str,
    ) -> bool:
        """
        Run a diagnosis-and-fix pass in the worktree.

        Returns True only when the diagnostic agent exited cleanly AND
        produced at least one new commit on the worktree's branch.
        """
        wt = Path(worktree_path)
        if not wt.exists():
            logger.error(f"DiagnosticAgent: worktree does not exist: {wt}")
            return False

        logger.info(f"DiagnosticAgent starting for '{task_title}' in {wt}")

        before_head = self._head_sha(wt)
        if before_head is None:
            logger.error(f"DiagnosticAgent: {wt} is not a git worktree we can inspect")
            return False

        prompt = self._build_prompt(task_title, task_description, logs)

        try:
            result = subprocess.run(
                [
                    self.claude_command,
                    "--print",
                    "--bare",
                    "--dangerously-skip-permissions",
                    prompt,
                ],
                cwd=str(wt),
                capture_output=True,
                text=True,
                timeout=DIAGNOSTIC_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.error(f"DiagnosticAgent timed out after {DIAGNOSTIC_TIMEOUT_SECONDS}s in {wt}")
            return False
        except FileNotFoundError:
            logger.error(f"DiagnosticAgent: command not found: {self.claude_command!r}")
            return False
        except Exception as e:
            logger.error(f"DiagnosticAgent subprocess failed: {e}")
            return False

        if result.returncode != 0:
            logger.error(
                f"DiagnosticAgent exited {result.returncode} in {wt}: "
                f"{result.stderr[-500:] if result.stderr else '(no stderr)'}"
            )
            return False

        after_head = self._head_sha(wt)
        if after_head is None or after_head == before_head:
            logger.warning(
                f"DiagnosticAgent in {wt} produced no new commits "
                f"(HEAD still {before_head[:8] if before_head else '?'}) — treating as failure"
            )
            return False

        logger.info(
            f"DiagnosticAgent committed fix in {wt}: "
            f"{before_head[:8]} -> {after_head[:8]}"
        )
        return True

    def _build_prompt(self, task_title: str, task_description: str, logs: str) -> str:
        return f"""You are a Diagnostic Agent. A previous AI agent failed to complete this task:

TASK TITLE: {task_title}
TASK DESCRIPTION: {task_description}

THE FAILED AGENT'S RECENT LOGS:
```
{logs}
```

You are running inside the failed agent's git worktree (your current
working directory). Your job is to:

1. Read the logs and the relevant files to figure out why the agent failed
   (e.g. syntax error, missing dependency, wrong path, broken test).
2. Apply a fix to the codebase in the current working directory.
3. Stage and commit your fix on the current branch with `git add` and
   `git commit`. The orchestrator considers the task fixed ONLY if HEAD
   advances — uncommitted edits will be reported as a failure.

When done, briefly explain what you changed.
"""

    def _head_sha(self, wt: Path) -> str | None:
        try:
            res = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"DiagnosticAgent: git rev-parse failed in {wt}: {e}")
            return None
        if res.returncode != 0:
            return None
        sha = res.stdout.strip()
        return sha or None
