"""A plan that marks its tasks keeps its other sections as notes for every
agent instead of starting an agent for each of them."""
from service.plan_parser import parse_plan

MARKED = """# Swarm Plan: tune retrieval

Ground rules for every arm:
- one change per arm

## How to measure
Run the eval harness; never score by eye.

## Task: Propose the slate
- depends_on: none

List three experiments.

## Task: Run arm A
- files: backend/retrieval.py
- depends_on: Propose the slate

Make the first change.
"""

UNMARKED = """# Plan

intro

## Build the API
do it

## Write the tests
Deps: [T1]
check it
"""


def _plan(tmp_path, text):
    path = tmp_path / "plan.md"
    path.write_text(text)
    return parse_plan(path)


def test_only_marked_sections_become_tasks(tmp_path):
    tasks = _plan(tmp_path, MARKED)
    assert [t.id for t in tasks] == ["propose-the-slate", "run-arm-a"]
    assert tasks[1].dependencies == ["propose-the-slate"]
    assert tasks[1].file_scope == ["backend/retrieval.py"]


def test_every_task_is_given_the_plans_notes(tmp_path):
    for task in _plan(tmp_path, MARKED):
        assert "one change per arm" in task.description
        assert "How to measure" in task.description and "never score by eye" in task.description
        assert "# Swarm Plan" not in task.description


def test_a_plan_without_marked_tasks_reads_as_before(tmp_path):
    tasks = _plan(tmp_path, UNMARKED)
    assert [t.id for t in tasks] == ["build-the-api", "write-the-tests"]
    assert all("Notes from the plan" not in t.description for t in tasks)


def test_a_numbered_reference_names_that_task(tmp_path):
    tasks = _plan(tmp_path, UNMARKED)
    assert tasks[1].dependencies == ["build-the-api"]
