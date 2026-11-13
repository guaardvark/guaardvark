"""Every parsed task gets a unique, non-empty id (it names a worktree and a branch)."""

from service.plan_parser import parse_plan


def test_task_ids_are_unique_and_non_empty(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Swarm Plan: ids\n\n"
        "## Task: Add tests\nOne.\n\n"
        "## Task: Add tests\nTwo.\n\n"
        "## Task: 测试\nThree.\n\n"
        "## Task: —\nFour.\n\n"
        "## Task: Build API\nFive.\n",
        encoding="utf-8",
    )
    ids = [t.id for t in parse_plan(plan)]
    assert ids == ["add-tests", "add-tests-2", "task-3", "task-4", "build-api"]
    assert all(ids) and len(set(ids)) == len(ids)
