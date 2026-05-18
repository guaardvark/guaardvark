"""
Merger Agent — specialized AI agent for autonomous conflict resolution.

When a branch merge fails with conflicts, the Merger Agent is called to
analyze the conflict markers (<<<<<<< HEAD) and resolve them based on
the intent of the swarm plan.
"""

import json
import logging
import os
import requests
import subprocess
from pathlib import Path

logger = logging.getLogger("swarm.merger")

class MergerAgent:
    """
    Specialized agent for resolving git merge conflicts.
    Calls the main Guaardvark LLM API to perform the resolution.
    """

    def __init__(self, backend_url: str):
        self.backend_url = backend_url

    def resolve_conflicts(
        self, 
        repo_path: Path, 
        branch_name: str, 
        conflict_files: list[str], 
        task_title: str,
        task_description: str
    ) -> bool:
        """
        Attempts to resolve conflicts in the given files.
        Assumes the repo is currently in a state with conflict markers.
        """
        logger.info(f"MergerAgent attempting to resolve conflicts in {len(conflict_files)} files for '{task_title}'")
        
        all_resolved = True
        for file_path in conflict_files:
            if not self._resolve_file(repo_path, file_path, task_title, task_description):
                all_resolved = False
                logger.warning(f"MergerAgent failed to resolve {file_path}")
        
        if all_resolved:
            # check if there are any remaining conflict markers
            for file_path in conflict_files:
                full_path = repo_path / file_path
                if full_path.exists():
                    content = full_path.read_text(errors="replace")
                    if "<<<<<<<" in content or ">>>>>>>" in content:
                        logger.warning(f"Conflict markers still present in {file_path}")
                        all_resolved = False
                        break
        
        if all_resolved:
            logger.info("MergerAgent successfully resolved all conflicts")
            return True
        return False

    def _resolve_file(self, repo_path: Path, rel_path: str, title: str, description: str) -> bool:
        full_path = repo_path / rel_path
        if not full_path.exists():
            return False

        conflicting_content = full_path.read_text(errors="replace")
        
        prompt = f"""
You are a Conflict Resolution Agent. Your task is to resolve git merge conflicts in the file: {rel_path}

The conflict occurred while merging a task titled: "{title}"
Task description: {description}

Below is the content of the file with git conflict markers (<<<<<<<, =======, >>>>>>>).
Please resolve the conflicts by merging the changes intelligently. 
Ensure the resulting code is syntactically correct and preserves the intent of both the current branch (HEAD) and the incoming changes.

FILE CONTENT:
```
{conflicting_content}
```

Provide the FULL resolved content of the file. Do not include any explanations, markdown markers (like ```), or other text. Just the raw, resolved file content.
"""

        try:
            # Call Guaardvark's internal LLM API
            # We use the 'instinct' mode for fast, single-call resolution
            response = requests.post(
                f"{self.backend_url}/enhanced-chat",
                json={
                    "message": prompt,
                    "chat_mode": "instinct",
                    "session_id": f"merger-{rel_path.replace('/', '-')}"
                },
                timeout=60
            )
            response.raise_for_status()
            data = response.json()
            
            # The enhanced-chat API returns the result in data['data']['response'] 
            # or directly in data['response'] depending on version.
            resolved_content = data.get("data", {}).get("response", data.get("response", ""))
            
            if not resolved_content or "<<<<<<<" in resolved_content:
                logger.warning(f"LLM returned invalid or unresolved content for {rel_path}")
                return False

            # Strip potential markdown wrapping if the LLM ignored instructions
            if resolved_content.startswith("```"):
                lines = resolved_content.split("\n")
                if lines[0].startswith("```"):
                    resolved_content = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])

            # Write the resolved content back to the file
            full_path.write_text(resolved_content)
            
            # git add the resolved file
            subprocess.run(["git", "-C", str(repo_path), "add", rel_path], check=True)
            return True

        except Exception as e:
            logger.error(f"Error calling LLM for conflict resolution: {e}")
            return False
