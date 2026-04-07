"""
Worktree Manager — git worktree lifecycle for swarm agents.

Each agent gets its own worktree (a full working copy of the repo on
its own branch) so they can all edit files without stepping on each
other. Worktrees share the .git directory so they're lightweight.

Layout:
    {repo}/.swarm-worktrees/{swarm_id}/{task_id}/    ← agent works here
    {repo}/.swarm-worktrees/{swarm_id}/manifest.json ← we track state here
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("swarm.worktree")


@dataclass
class WorktreeInfo:
    """What we know about one worktree."""

    task_id: str
    swarm_id: str
    branch_name: str
    worktree_path: str
    created: bool = False  # did we actually create it yet?

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorktreeManifest:
    """Tracks all worktrees for a swarm run."""

    swarm_id: str
    repo_path: str
    base_branch: str
    worktrees: dict[str, WorktreeInfo] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "repo_path": self.repo_path,
            "base_branch": self.base_branch,
            "worktrees": {k: v.to_dict() for k, v in self.worktrees.items()},
        }


class WorktreeManager:
    """
    Manages git worktrees for swarm agents.

    Create one of these per swarm run. It handles creating worktrees,
    tracking them, and cleaning them up when you're done.
    """

    def __init__(self, repo_path: str | Path, swarm_id: str, worktree_base: str = ".swarm-worktrees"):
        self.repo_path = Path(repo_path).resolve()
        self.swarm_id = swarm_id
        self.swarm_dir = self.repo_path / worktree_base / swarm_id
        self.manifest_path = self.swarm_dir / "manifest.json"

        # figure out what branch we're on — that's the base for all worktrees
        self.base_branch = self._get_current_branch()

        self.manifest = WorktreeManifest(
            swarm_id=swarm_id,
            repo_path=str(self.repo_path),
            base_branch=self.base_branch,
        )

        # make sure .swarm-worktrees is in .git/info/exclude so it never gets committed
        self._ensure_git_excluded(worktree_base)

    def create(self, task_id: str) -> WorktreeInfo:
        """
        Create a worktree for a task.

        Returns a WorktreeInfo with the path and branch name.
        The worktree starts as a copy of the current branch.
        """
        branch_name = f"swarm/{self.swarm_id}/{task_id}"
        worktree_path = self.swarm_dir / task_id

        if worktree_path.exists():
            logger.warning(f"Worktree already exists at {worktree_path}, reusing")
            info = WorktreeInfo(
                task_id=task_id,
                swarm_id=self.swarm_id,
                branch_name=branch_name,
                worktree_path=str(worktree_path),
                created=True,
            )
            self.manifest.worktrees[task_id] = info
            self._save_manifest()
            return info

        # create the directory structure
        self.swarm_dir.mkdir(parents=True, exist_ok=True)

        # git worktree add -b <branch> <path>
        result = self._git(
            "worktree", "add", "-b", branch_name, str(worktree_path),
            check=False,
        )

        if result.returncode != 0:
            # branch might already exist from a previous run — try without -b
            logger.info(f"Branch {branch_name} might exist, trying checkout instead")
            result = self._git(
                "worktree", "add", str(worktree_path), branch_name,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to create worktree for {task_id}: {result.stderr}"
                )

        info = WorktreeInfo(
            task_id=task_id,
            swarm_id=self.swarm_id,
            branch_name=branch_name,
            worktree_path=str(worktree_path),
            created=True,
        )
        self.manifest.worktrees[task_id] = info
        self._save_manifest()

        logger.info(f"Created worktree: {worktree_path} on branch {branch_name}")
        return info

    def list_worktrees(self) -> list[WorktreeInfo]:
        """List all worktrees for this swarm."""
        return list(self.manifest.worktrees.values())

    def get_worktree(self, task_id: str) -> WorktreeInfo | None:
        """Get info about a specific worktree."""
        return self.manifest.worktrees.get(task_id)

    def cleanup(self, task_id: str, delete_branch: bool = False) -> bool:
        """
        Remove a worktree and optionally its branch.

        Returns True if cleanup succeeded.
        """
        info = self.manifest.worktrees.get(task_id)
        if not info:
            logger.warning(f"No worktree found for task {task_id}")
            return False

        worktree_path = Path(info.worktree_path)

        # remove the git worktree reference
        self._git("worktree", "remove", str(worktree_path), "--force", check=False)

        # nuke the directory if git didn't clean it up
        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)

        # optionally delete the branch
        if delete_branch and info.branch_name:
            self._git("branch", "-D", info.branch_name, check=False)
            logger.info(f"Deleted branch {info.branch_name}")

        del self.manifest.worktrees[task_id]
        self._save_manifest()

        logger.info(f"Cleaned up worktree for task {task_id}")
        return True

    def cleanup_all(self, delete_branches: bool = False) -> int:
        """
        Remove all worktrees for this swarm. Returns count of cleaned up worktrees.

        The nuclear option — for when a swarm goes sideways.
        """
        task_ids = list(self.manifest.worktrees.keys())
        count = 0
        for task_id in task_ids:
            if self.cleanup(task_id, delete_branch=delete_branches):
                count += 1

        # prune any orphaned worktree refs
        self._git("worktree", "prune", check=False)

        # remove the swarm directory itself
        if self.swarm_dir.exists():
            shutil.rmtree(self.swarm_dir, ignore_errors=True)

        logger.info(f"Cleaned up {count} worktrees for swarm {self.swarm_id}")
        return count

    def disk_usage_mb(self) -> float:
        """How much disk space are we using? People worry about this."""
        if not self.swarm_dir.exists():
            return 0.0
        total = sum(f.stat().st_size for f in self.swarm_dir.rglob("*") if f.is_file())
        return total / (1024 * 1024)

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command in the repo."""
        cmd = ["git", "-C", str(self.repo_path), *args]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if check and result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
        return result

    def _get_current_branch(self) -> str:
        """What branch is the main repo on right now?"""
        result = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    def _ensure_git_excluded(self, worktree_base: str) -> None:
        """
        Add .swarm-worktrees to .git/info/exclude so it never shows up in git status.

        We do NOT touch .gitignore — that's the user's file.
        """
        exclude_file = self.repo_path / ".git" / "info" / "exclude"
        exclude_file.parent.mkdir(parents=True, exist_ok=True)

        pattern = f"/{worktree_base}/"
        if exclude_file.exists():
            content = exclude_file.read_text()
            if pattern in content:
                return  # already excluded
            # append to existing file
            with open(exclude_file, "a") as f:
                f.write(f"\n# Swarm plugin worktrees\n{pattern}\n")
        else:
            exclude_file.write_text(f"# Swarm plugin worktrees\n{pattern}\n")

        logger.debug(f"Added {pattern} to .git/info/exclude")

    def _save_manifest(self) -> None:
        """Persist the manifest to disk."""
        self.swarm_dir.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "w") as f:
            json.dump(self.manifest.to_dict(), f, indent=2)

    @classmethod
    def load_existing(cls, repo_path: str | Path, swarm_id: str, worktree_base: str = ".swarm-worktrees") -> WorktreeManager | None:
        """
        Load a WorktreeManager from an existing manifest.

        Returns None if no manifest found (swarm doesn't exist or was cleaned up).
        """
        repo_path = Path(repo_path).resolve()
        manifest_path = repo_path / worktree_base / swarm_id / "manifest.json"

        if not manifest_path.exists():
            return None

        with open(manifest_path) as f:
            data = json.load(f)

        mgr = cls(repo_path, swarm_id, worktree_base)
        mgr.manifest.base_branch = data.get("base_branch", mgr.base_branch)

        for task_id, wt_data in data.get("worktrees", {}).items():
            mgr.manifest.worktrees[task_id] = WorktreeInfo(
                task_id=wt_data["task_id"],
                swarm_id=wt_data["swarm_id"],
                branch_name=wt_data["branch_name"],
                worktree_path=wt_data["worktree_path"],
                created=wt_data.get("created", True),
            )

        logger.info(f"Loaded existing swarm manifest: {swarm_id} ({len(mgr.manifest.worktrees)} worktrees)")
        return mgr
