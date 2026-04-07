"""
Merge Manager — handles merging completed agent branches back to base.

Merges in dependency order (foundations first), runs conflict checks
before attempting, and flags anything messy for human review instead
of forcing it. Because nobody wants an auto-merge that silently
breaks everything.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .models import MergeResult, SwarmTask, SwarmStatus

logger = logging.getLogger("swarm.merge")


class MergeManager:
    """
    Manages branch merging for completed swarm tasks.

    Call check_conflicts() first to see if it'll be clean,
    then attempt_merge() to actually do it.
    """

    def __init__(self, repo_path: str | Path, base_branch: str):
        self.repo_path = Path(repo_path).resolve()
        self.base_branch = base_branch

    def check_conflicts(self, branch_name: str) -> MergeResult:
        """
        Dry-run merge to see if this branch can merge cleanly.

        Does NOT modify anything — just checks and reports back.
        """
        task_id = branch_name.split("/")[-1] if "/" in branch_name else branch_name

        # make sure we're on the base branch
        current = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if current != self.base_branch:
            self._git("checkout", self.base_branch, check=False)

        # try a dry-run merge
        result = self._git(
            "merge", "--no-commit", "--no-ff", branch_name,
            check=False,
        )

        if result.returncode == 0:
            # clean merge — abort it since this was just a check
            self._git("merge", "--abort", check=False)
            return MergeResult(task_id=task_id, success=True)
        else:
            # conflicts — figure out which files
            conflict_files = self._get_conflict_files()
            self._git("merge", "--abort", check=False)

            return MergeResult(
                task_id=task_id,
                success=False,
                conflict_files=conflict_files,
                error=result.stderr.strip() if result.stderr else "Merge conflicts detected",
            )

    def attempt_merge(
        self,
        task: SwarmTask,
        run_tests: bool = False,
        test_command: str = "python3 -m pytest",
    ) -> MergeResult:
        """
        Actually merge a completed task's branch into base.

        If run_tests is True, runs the test command in the worktree first
        and refuses to merge if tests fail. Because merging broken code
        is worse than not merging at all.
        """
        if not task.branch_name:
            return MergeResult(
                task_id=task.id, success=False,
                error="No branch name — task was never launched",
            )

        # optionally run tests in the worktree before merging
        if run_tests and task.worktree_path:
            test_passed = self._run_tests(task.worktree_path, test_command)
            if not test_passed:
                return MergeResult(
                    task_id=task.id, success=False,
                    error=f"Tests failed in worktree — refusing to merge",
                )

        # check for conflicts first
        check = self.check_conflicts(task.branch_name)
        if not check.success:
            task.status = SwarmStatus.NEEDS_REVIEW
            return check

        # actually merge
        current = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if current != self.base_branch:
            self._git("checkout", self.base_branch)

        result = self._git(
            "merge", "--no-ff", task.branch_name,
            "-m", f"swarm: merge {task.id} — {task.title}",
            check=False,
        )

        if result.returncode == 0:
            task.status = SwarmStatus.MERGED
            logger.info(f"Merged {task.branch_name} into {self.base_branch}")
            return MergeResult(task_id=task.id, success=True)
        else:
            # something went wrong despite the check passing (race condition?)
            self._git("merge", "--abort", check=False)
            task.status = SwarmStatus.NEEDS_REVIEW
            return MergeResult(
                task_id=task.id, success=False,
                conflict_files=self._get_conflict_files(),
                error=result.stderr.strip(),
            )

    def merge_queue(self, tasks: list[SwarmTask]) -> list[SwarmTask]:
        """
        Order tasks for merging based on their dependency graph.

        Tasks with no dependencies get merged first (foundations),
        then tasks that depended on them, etc. This minimizes the
        chance of conflicts since foundational changes land first.
        """
        # only merge tasks that are done
        mergeable = [t for t in tasks if t.status == SwarmStatus.DONE and t.branch_name]

        if not mergeable:
            return []

        # topological sort — tasks with fewer/no deps first
        merged_ids: set[str] = set()
        ordered: list[SwarmTask] = []
        remaining = list(mergeable)

        # keep going until we've ordered everything (or detected a cycle)
        max_iterations = len(remaining) + 1
        for _ in range(max_iterations):
            if not remaining:
                break

            # find tasks whose deps are all satisfied
            ready = [
                t for t in remaining
                if all(d in merged_ids for d in t.dependencies)
            ]

            if not ready:
                # everything left has unmet deps — just merge in original order
                logger.warning("Dependency cycle detected in merge queue — falling back to plan order")
                ordered.extend(remaining)
                break

            for t in ready:
                ordered.append(t)
                merged_ids.add(t.id)
                remaining.remove(t)

        return ordered

    def get_branch_diff_stats(self, branch_name: str) -> dict[str, int]:
        """
        Quick stats on what a branch changed — useful for the dashboard.

        Returns dict with files_changed, insertions, deletions.
        """
        result = self._git(
            "diff", "--stat", f"{self.base_branch}...{branch_name}",
            check=False,
        )

        stats = {"files_changed": 0, "insertions": 0, "deletions": 0}

        if result.returncode != 0:
            return stats

        # parse the summary line at the end of git diff --stat
        lines = result.stdout.strip().split("\n")
        if lines:
            summary = lines[-1]
            import re
            files_match = re.search(r"(\d+)\s+files?\s+changed", summary)
            insert_match = re.search(r"(\d+)\s+insertions?", summary)
            delete_match = re.search(r"(\d+)\s+deletions?", summary)

            if files_match:
                stats["files_changed"] = int(files_match.group(1))
            if insert_match:
                stats["insertions"] = int(insert_match.group(1))
            if delete_match:
                stats["deletions"] = int(delete_match.group(1))

        return stats

    # -------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git", "-C", str(self.repo_path), *args]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if check and result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
        return result

    def _get_conflict_files(self) -> list[str]:
        """Get list of files with merge conflicts."""
        result = self._git("diff", "--name-only", "--diff-filter=U", check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")
        return []

    def _run_tests(self, worktree_path: str, test_command: str) -> bool:
        """Run tests in a worktree. Returns True if they pass."""
        logger.info(f"Running tests in {worktree_path}: {test_command}")
        try:
            result = subprocess.run(
                test_command.split(),
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout for tests
            )
            if result.returncode == 0:
                logger.info(f"Tests passed in {worktree_path}")
                return True
            else:
                logger.warning(f"Tests failed in {worktree_path}: {result.stdout[-500:]}")
                return False
        except subprocess.TimeoutExpired:
            logger.warning(f"Tests timed out in {worktree_path}")
            return False
        except Exception as e:
            logger.warning(f"Could not run tests in {worktree_path}: {e}")
            return False
