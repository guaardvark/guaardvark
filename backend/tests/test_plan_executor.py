"""How a plan step's outcome is judged, without a model or database.

The step watch reads the step turn's events; the executor decides from what
it saw whether the step worked, failed, or was already done by an earlier
step.
"""
from backend.services import plan_executor as pe


def _complete(watch, calls, response="done"):
    watch("chat:complete", {"response": response, "steps": [{"tool_calls": calls}]})


def test_the_watch_keeps_the_files_a_step_made():
    sent = []
    watch = pe._StepWatch(lambda event, payload: sent.append((event, payload)), "p1", 1)
    _complete(watch, [
        {"tool_name": "generate_csv", "success": True,
         "params": {"filename": "planets.csv"}, "output_preview": "{'content': 'Planet,Diameter'"},
        {"tool_name": "generate_file", "success": False, "params": {"filename": "failed.md"}},
    ])
    assert watch.made == ["planets.csv"]
    assert watch.tool_ok == 1
    # The step's chat:complete must not end the run in the browser.
    assert [event for event, _ in sent] == ["chat:plan_step_done"]
    assert sent[0][1]["plan_id"] == "p1" and sent[0][1]["plan_step"] == 1


def test_a_step_whose_file_an_earlier_step_made_is_done():
    step = {"title": "Save planets.csv", "action": "Create a new file named 'planets.csv' with the CSV text"}
    assert pe._made_earlier(step, {"planets.csv": 1}) == "planets.csv was already made in step 1."


def test_a_step_that_names_no_earlier_file_still_fails():
    step = {"title": "Make a dog picture", "action": "Make a picture of a dog and save it as dog.png"}
    assert pe._made_earlier(step, {"planets.csv": 1}) == ""
    assert pe._step_error({"kind": "image"}, {}, pe._StepWatch(lambda *a: None, "p1", 2)) == (
        "Nothing ran for this step: no tool was called.")


def test_text_steps_need_no_tool():
    watch = pe._StepWatch(lambda *a: None, "p1", 1)
    assert pe._step_error({"kind": "write"}, {}, watch) == ""
    assert pe._step_error({"kind": "lookup"}, {}, watch) == ""
