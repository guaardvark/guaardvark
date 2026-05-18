"""
Diagnostic Agent — specialized agent for analyzing and fixing task failures.

When an agent fails a task (crashes or exhausts retries), the Diagnostic
Agent is spawned in the same worktree to read the logs, understand the
failure, and attempt a fix.
"""

import json
import logging
import os
import requests
from pathlib import Path

logger = logging.getLogger("swarm.diagnostic")

class DiagnosticAgent:
    """
    Analyzes agent logs and attempts to fix the codebase so the task
    can be completed successfully.
    """

    def __init__(self, backend_url: str):
        self.backend_url = backend_url

    def run_diagnosis(
        self,
        worktree_path: str,
        task_title: str,
        task_description: str,
        logs: str
    ) -> bool:
        """
        Runs the diagnosis and attempt-fix loop.
        Returns True if it believes it fixed the issue.
        """
        logger.info(f"DiagnosticAgent starting for '{task_title}' in {worktree_path}")
        
        prompt = f"""
You are a Diagnostic Agent. An AI agent failed to complete the following task:

TASK TITLE: {task_title}
TASK DESCRIPTION: {task_description}

THE AGENT'S RECENT LOGS:
```
{logs}
```

Your goal is to:
1. Analyze the logs to understand why the agent failed (e.g., syntax error, missing dependency, logic error).
2. Apply a fix to the codebase in the current directory to resolve the issue.
3. Ensure the task can now be completed (or is actually completed by your fix).

You are working in a git worktree. When you have fixed the issue, explain what you did briefly.

I will provide you with the tool to execute bash commands and read/write files.
"""

        # Since this agent needs to be interactive/multi-step to actually fix files,
        # we delegate to the main 'deliberation' mode of Guaardvark's chat engine.
        # This allows it to use all 70+ tools (read_file, replace, run_shell_command, etc.)
        try:
            # We use the 'deliberation' mode because fixing bugs requires multiple tool steps.
            # We call the 'unified' chat API which supports ReACT loops.
            response = requests.post(
                f"{self.backend_url}/chat/unified",
                json={
                    "message": prompt,
                    "chat_mode": "deliberation",
                    "session_id": f"diagnostic-{task_title.replace(' ', '-')}",
                    "stream": False # We want the final result
                },
                timeout=300 # Diagnosis can take a while
            )
            response.raise_for_status()
            data = response.json()
            
            # If the LLM returns successfully, we assume it at least tried to fix it.
            # In a more advanced version, we would verify the fix here (e.g. run tests).
            logger.info(f"DiagnosticAgent finished for '{task_title}'")
            return True

        except Exception as e:
            logger.error(f"DiagnosticAgent failed: {e}")
            return False
