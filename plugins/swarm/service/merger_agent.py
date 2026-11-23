"""
Merger Agent — specialized AI agent for autonomous conflict resolution.

When a branch merge fails with conflicts, the Merger Agent is called to
analyze the conflict markers (<<<<<<< HEAD) and resolve them based on
the intent of the swarm plan.
"""

import hashlib
import json
import logging
import os
import requests
from pathlib import Path

logger = logging.getLogger("swarm.merger")


def _guaardvark_root() -> Path | None:
    """Resolve the configured Guaardvark repo root, if any."""
    root = os.environ.get("GUAARDVARK_ROOT")
    if root:
        try:
            return Path(root).expanduser().resolve()
        except Exception:
            return None
    return None


def _is_under_repo_root(path: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False

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
                    # One session per file, hashed so a long path still fits the 36-character id.
                    "session_id": f"merger-{hashlib.sha1(rel_path.encode()).hexdigest()[:16]}"
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

        except Exception as e:
            logger.error(f"Error calling LLM for conflict resolution: {e}")
            return False

        # --- Write the resolution ---
        # Targets inside the Guaardvark repo are funneled through the guarded-code
        # chokepoint (apply_exact_replacement) so every code write goes through the
        # same backup/syntax-verify/rollback path as the rest of the system. There
        # is no git add here: merge_manager stages each resolved path and commits.
        root = _guaardvark_root()
        if not _is_under_repo_root(full_path, root):
            # Outside the repo root: refuse — the merger has no business writing there.
            logger.error(f"MergerAgent refusing to write outside repo root: {full_path}")
            return False

        try:
            from backend.services.guarded_code_service import (
                apply_exact_replacement,
                GuardedCodeError,
            )
        except Exception as e:
            logger.error(f"guarded_code_service unavailable; refusing raw write for {rel_path}: {e}")
            return False

        try:
            apply_exact_replacement(
                str(full_path),
                old_text=conflicting_content,
                new_text=resolved_content,
                repo_root=str(root),
                allow_reindent=False,
            )
            return True
        except GuardedCodeError as e:
            logger.warning(f"Guarded write rejected for {rel_path} (NEEDS_REVIEW): {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during guarded write for {rel_path}: {e}")
            return False
