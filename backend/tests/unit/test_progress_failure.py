"""A failing Celery task marks its progress entry errored instead of leaving it at 0 %."""

from __future__ import annotations

from backend.utils.progress_failure import candidate_ids, failure_message, mark_progress_failed


class _Progress:
    def __init__(self, known):
        self.known = set(known)
        self.errors = []

    def get_process(self, pid):
        return object() if pid in self.known else None

    def error_process(self, pid, message):
        self.errors.append((pid, message))
        return True


def test_candidate_ids_prefer_the_task_id_then_job_arguments():
    assert list(candidate_ids("celery-1", {"job_id": "job-9", "process_id": "job-9", "x": 1})) == ["celery-1", "job-9"]
    assert list(candidate_ids(None, {"process_id": "p"})) == ["p"]
    assert list(candidate_ids(None, None)) == []


def test_failure_message_names_the_exception_and_is_bounded():
    assert failure_message(RuntimeError("CUDA not available")) == "RuntimeError: CUDA not available"
    assert len(failure_message(ValueError("x" * 2000))) == 500
    assert failure_message(None) == "Task failed"


def test_mark_progress_failed_hits_the_entry_the_task_created():
    progress = _Progress({"job-9"})
    marked = mark_progress_failed("celery-1", RuntimeError("boom"), {"job_id": "job-9"}, progress=progress)
    assert marked == "job-9"
    assert progress.errors == [("job-9", "RuntimeError: boom")]


def test_mark_progress_failed_is_a_no_op_without_an_entry():
    progress = _Progress(set())
    assert mark_progress_failed("celery-1", RuntimeError("boom"), {"job_id": "job-9"}, progress=progress) is None
    assert progress.errors == []
