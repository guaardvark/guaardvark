"""
Merge Manager — handles merging completed agent branches back to base.

Merges in dependency order (foundations first), runs conflict checks
before attempting, and flags anything messy for human review instead
of forcing it. Because nobody wants an auto-merge that silently
breaks everything.
"""

from __future__ import annotations

import importlib
import logging
import subprocess
import sys
from pathlib import Path, PurePosixPath

from .models import MergeResult, SwarmTask, SwarmStatus

logger = logging.getLogger("swarm.merge")


def _test_env() -> dict[str, str]:
    """Environment for a pre-merge test run: an agent's branch is running, so
    it gets no secrets and a database URL that reaches nothing."""
    from .agent_backends.base_backend import agent_env

    env = agent_env()
    env.update(DATABASE_URL="postgresql://swarm-merge-tests@127.0.0.1:1/none", GUAARDVARK_MODE="test")
    return env

# The inbound guard ships with the Guaardvark checkout this plugin lives in.
_GUAARDVARK_ROOT = Path(__file__).resolve().parents[3]


def _inbound_guard():
    """The inbound guard engine, or None when this checkout has none."""
    if not (_GUAARDVARK_ROOT / "scripts" / "inbound_guard" / "__init__.py").is_file():
        return None
    if str(_GUAARDVARK_ROOT) not in sys.path:
        sys.path.insert(0, str(_GUAARDVARK_ROOT))
    try:
        return importlib.import_module("scripts.inbound_guard")
    except Exception as exc:
        logger.warning(f"Inbound guard unavailable: {exc}")
        return None


class MergeManager:
    """
    Manages branch merging for completed swarm tasks.

    Call check_conflicts() first to see if it'll be clean,
    then attempt_merge() to actually do it.
    """

    def __init__(
        self, 
        repo_path: str | Path, 
        base_branch: str,
        enable_merger_agent: bool = False,
        backend_url: str | None = None
    ):
        self.repo_path = Path(repo_path).resolve()
        self.base_branch = base_branch
        self.enable_merger_agent = enable_merger_agent
        self.backend_url = backend_url
        self._merger = None

        if self.enable_merger_agent and self.backend_url:
            from .merger_agent import MergerAgent
            self._merger = MergerAgent(self.backend_url)

    def _inbound_check(self, task: SwarmTask) -> MergeResult | None:
        """Judge the merge result with the inbound guard; a MergeResult when it refuses.

        ``git merge-tree --write-tree`` builds the merged tree as objects only, so
        nothing reaches the working tree until the guard has read it. Mode comes
        from GUAARDVARK_INBOUND_GUARD or `git config inboundguard.mode`, which the
        Settings page writes; off means this does nothing.
        """
        guard = _inbound_guard()
        if guard is None:
            return None
        mode = guard.git_mode(self.repo_path)
        if mode == "off":
            return None
        try:
            tree = self._git("merge-tree", "--write-tree", self.base_branch, task.branch_name, check=False)
            merged = tree.stdout.split()[0] if tree.stdout.strip() else ""
            if not merged:
                raise RuntimeError(tree.stderr.strip() or "merge-tree produced no tree")
            verdict = guard.scan_diff(self.repo_path, [self.base_branch, merged], source="swarm",
                                      subject=f"swarm {task.id}: {task.branch_name}", mode=mode)
            from scripts.inbound_guard import ledger
            from scripts.inbound_guard.sources import git_common_dir

            path = ledger.ledger_path(git_common_dir(self.repo_path))
            data = verdict.to_dict()
            data["kind"] = "verdict"
            ledger.append(path, data)
            approved = ledger.approval_for(path, verdict.digest) is not None
        except Exception as exc:
            logger.error(f"Inbound guard could not read {task.branch_name}: {exc}")
            if mode == "enforce":
                return MergeResult(task_id=task.id, success=False,
                                   error=f"The inbound guard could not read this branch ({exc}); not merging.")
            return None
        if verdict.findings:
            logger.info(f"Inbound guard on {task.branch_name}: {verdict.verdict}, "
                        f"{len(verdict.findings)} finding(s)")
        if not verdict.enforced or approved:
            return None
        worst = verdict.findings[0]
        where = f"{worst.path}:{worst.line}" if worst.line else worst.path
        return MergeResult(
            task_id=task.id, success=False,
            error=(f"{'Held' if verdict.verdict == 'hold' else 'Blocked'} by the inbound guard: "
                   f"{len(verdict.findings)} finding(s), worst {worst.severity} {worst.rule} at {where}. "
                   f"Approve with: python3 scripts/check_inbound.py approve {verdict.digest} --note \"why\""),
        )

    def check_conflicts(self, branch_name: str) -> MergeResult:
        """
        Dry-run merge to see if this branch can merge cleanly.

        Does NOT modify anything — just checks and reports back.
        """
        task_id = branch_name.split("/")[-1] if "/" in branch_name else branch_name

        off_base = self._off_base_branch()
        if off_base:
            return MergeResult(task_id=task_id, success=False, error=off_base)

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

        # Read what the branch brings in before its tests run its code or the
        # conflict check writes it into the checkout.
        refused = self._inbound_check(task)
        if refused is not None:
            task.status = SwarmStatus.NEEDS_REVIEW
            return refused

        # optionally run tests in the worktree before merging
        if run_tests and task.worktree_path:
            # The tests that cover what the branch changed, run in its
            # worktree. The whole suite does not finish inside the time limit
            # on a large repository, which read as "tests failed" every time.
            touched = self._git("diff", "--name-only", f"{self.base_branch}...{task.branch_name}",
                                check=False).stdout.splitlines()
            targets = self._test_targets(touched)
            if targets and not self._run_tests(task.worktree_path, test_command, targets):
                return MergeResult(
                    task_id=task.id, success=False,
                    error=f"Tests failed in the worktree ({' '.join(targets)}); not merged",
                )
            if not targets:
                logger.info(f"No tests cover the files {task.id} changed; merging without a test run")

        # check for conflicts first
        check = self.check_conflicts(task.branch_name)
        
        # If conflicts found and MergerAgent is enabled, try to resolve them
        if not check.success and self._merger:
            return self._merge_with_resolution(task, check, run_tests, test_command)

        if not check.success:
            task.status = SwarmStatus.NEEDS_REVIEW
            return check

        # actually merge
        off_base = self._off_base_branch()
        if off_base:
            return MergeResult(task_id=task.id, success=False, error=off_base)

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

    def _merge_with_resolution(
        self,
        task: SwarmTask,
        check: MergeResult,
        run_tests: bool = False,
        test_command: str = "python3 -m pytest",
    ) -> MergeResult:
        """Merge ``task``'s branch with the merger agent resolving the conflicts.

        Commits only when every conflicted path was resolved and staged and,
        with ``run_tests``, the tests for the files the merge touches pass on
        the resolved tree. Any other outcome, an exception included, runs
        ``git merge --abort`` so the checkout is never left mid-merge, and
        leaves the task for review.
        """
        logger.info(f"Conflict detected for {task.id}, invoking MergerAgent...")
        off_base = self._off_base_branch()
        if off_base:
            return MergeResult(task_id=task.id, success=False, error=off_base)
        try:
            self._git("merge", "--no-ff", "--no-commit", task.branch_name, check=False)
            conflicts = self._get_conflict_files() or check.conflict_files

            if not self._merger.resolve_conflicts(
                self.repo_path, task.branch_name, conflicts, task.title, task.description,
            ):
                return self._abort_for_review(task, check, "MergerAgent could not resolve every conflict")

            # The merger writes the resolved content; git still lists the paths
            # as unmerged until they are added, and refuses to commit before that.
            for path in conflicts:
                self._git("add", "--", path)
            unmerged = self._get_conflict_files()
            if unmerged:
                return self._abort_for_review(
                    task, check, f"Still unmerged after resolution: {', '.join(unmerged)}")

            if run_tests:
                failure = self._test_resolved_merge(test_command)
                if failure:
                    return self._abort_for_review(task, check, failure)

            self._git("commit", "-m", f"swarm: merge {task.id} (resolved by MergerAgent)")
        except Exception as exc:
            logger.error(f"Merge with MergerAgent failed for {task.id}: {exc}")
            return self._abort_for_review(task, check, f"Merge with MergerAgent failed: {exc}")

        logger.info(f"MergerAgent resolved conflicts for {task.id}; merged.")
        task.status = SwarmStatus.MERGED
        return MergeResult(task_id=task.id, success=True)

    def _test_resolved_merge(self, test_command: str) -> str | None:
        """Run the tests for the files this merge touches, on the resolved tree.

        Only those test files are passed to the test command, never the whole
        suite: this is the main checkout, where a full run can reach the live
        database. A merge no test covers is not committed untested. Returns
        the reason to abort, or None when the tests passed.
        """
        touched = self._git("diff", "--cached", "--name-only", "HEAD").stdout.splitlines()
        targets = self._test_targets(touched)
        if not targets:
            return ("No tests cover the files this merge touches, so the "
                    "MergerAgent's resolution was not committed untested")
        if not self._run_tests(str(self.repo_path), test_command, targets):
            return f"Tests failed on the resolved merge: {' '.join(targets)}"
        return None

    def _test_targets(self, touched: list[str]) -> list[str]:
        """Test files for ``touched`` paths: the test files among them, and
        test_<name>.py / <name>_test.py anywhere in the repo for each touched
        Python module."""
        by_name: dict[str, list[str]] = {}
        for path in self._git("ls-files", check=False).stdout.splitlines():
            by_name.setdefault(PurePosixPath(path).name, []).append(path)

        targets: list[str] = []
        for path in touched:
            name = PurePosixPath(path).name
            if not name.endswith(".py"):
                continue
            if name.startswith("test_") or name.endswith("_test.py"):
                candidates = [path]
            else:
                stem = name[:-3]
                candidates = by_name.get(f"test_{stem}.py", []) + by_name.get(f"{stem}_test.py", [])
            for candidate in candidates:
                if candidate not in targets and (self.repo_path / candidate).is_file():
                    targets.append(candidate)
        return targets

    def _abort_for_review(self, task: SwarmTask, check: MergeResult, reason: str) -> MergeResult:
        """Abort the merge in progress and leave the task for a person."""
        logger.warning(f"{reason} ({task.id}); aborting the merge.")
        self._git("merge", "--abort", check=False)
        task.status = SwarmStatus.NEEDS_REVIEW
        return MergeResult(task_id=task.id, success=False,
                           conflict_files=check.conflict_files, error=reason)

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

    def _off_base_branch(self) -> str | None:
        """Why a merge cannot run here, or None. The checkout is the running
        install: its branch is the person's to change, never switched for a
        merge."""
        current = self._git("rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
        if current == self.base_branch:
            return None
        return (f"The repository is on '{current}', and this swarm branched from '{self.base_branch}'. "
                f"Switch back to '{self.base_branch}' and merge again; the task branches are kept.")

    def _get_conflict_files(self) -> list[str]:
        """Get list of files with merge conflicts."""
        result = self._git("diff", "--name-only", "--diff-filter=U", check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")
        return []

    def _run_tests(self, worktree_path: str, test_command: str, targets: list[str] | None = None) -> bool:
        """Run tests in a worktree, limited to ``targets`` when given. Returns True if they pass."""
        command = test_command.split() + list(targets or [])
        logger.info(f"Running tests in {worktree_path}: {' '.join(command)}")
        try:
            result = subprocess.run(
                command,
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout for tests
                env=_test_env(),
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
