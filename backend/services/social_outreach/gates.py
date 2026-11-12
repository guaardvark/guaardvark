"""
What the independent check concluded about an outreach draft.

Every draft waits for a person's approval; no grade, however good, posts one.
The drafter grades its own text, and that score leans toward whatever it just
wrote, so a second model (external_grader) checks it blind. Its answer is
recorded with the draft for the person who reviews it; a draft that failed
the check is marked so they can see why.

Only a build of another model family counts as an independent check
(decision_data.INDEPENDENCE). A grader that resolves to the drafter's own
build (same Ollama manifest digest) or to another build of its family still
reports its answer, but a pass from it is labelled not_independent, never
passed.

A self-share has no thread for the rubric to judge, so it is never graded.

Used by content_agent (Recon candidates) and POST /draft-comment (Reddit loop,
self-share, Discord cog, Outreach page).
"""

from __future__ import annotations


def independent_check_label(ext: dict) -> str:
    """passed, not_independent, failed or unavailable: what the independent
    check concluded.

    ``ext`` is a grade_draft_externally result. Only ``checked`` counts as a
    check having run; a result without it is unavailable. A check that ran
    passed only when its ``passed`` is True: every question its rubric asked
    was answered yes. Its 0-1 grade is for display and does not decide. A pass
    from the drafter's own model (``independent`` False) is not_independent; a
    no from it is still failed. ``independent`` is set by the grader
    (external_grader._independence); this module only reads it.
    """
    ext = ext or {}
    if not ext.get("checked"):
        return "unavailable"
    if ext.get("passed") is not True:
        return "failed"
    return "not_independent" if ext.get("independent") is False else "passed"


def independent_ok(
    ext: dict,
    *,
    action: str = "comment",
    relevance_unchecked: bool = False,
) -> tuple[bool, str]:
    """Whether the independent check found nothing against this draft, and
    what to tell the person who reviews it.

    ``action`` is the audit row's action; a "share" or "post" (the account's
    own post, with no thread under it) is never graded. Nothing
    posts on this answer either way. ``relevance_unchecked`` says the
    thread-fit judge (external_grader.score_thread_relevance) could not run on
    the thread.

    Returns one of:
      (True,  "passed")               checked and passed, and the thread-fit
                                      judge ran (independent_check_label)
      (False, "failed")               checked and did not pass
      (True,  "not_independent")      passed, but the grader is the drafter's
                                      own model, so no independent check ran
      (True,  "relevance_unchecked")  checked and passed; the thread-fit judge
                                      did not run
      (True,  "unchecked")            the check could not run
      (True,  "share_not_graded")     a share
    """
    if action in ("share", "post"):
        return True, "share_not_graded"
    label = independent_check_label(ext)
    if label == "failed":
        return False, "failed"
    if label == "unavailable":
        return True, "unchecked"
    if label == "not_independent":
        return True, "not_independent"
    return True, "relevance_unchecked" if relevance_unchecked else "passed"
