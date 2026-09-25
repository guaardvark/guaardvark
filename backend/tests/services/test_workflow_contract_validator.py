"""The workflow contract checker itself: it must catch each way a graph can
break, and its /object_info snapshot must cover every class the builders emit."""
import copy
import importlib.util
from pathlib import Path

import pytest

from backend.tests.fixtures import workflow_contract as wc

ROOT = Path(__file__).resolve().parents[3]


def _snapshot_script():
    spec = importlib.util.spec_from_file_location("snapshot", ROOT / "scripts" / "comfyui_object_info_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_covers_every_class_the_builders_emit():
    missing = [c for c in _snapshot_script().builder_class_types() if c not in wc.object_info()]
    assert not missing, (
        f"not in backend/tests/fixtures/comfyui_object_info.json: {missing}. Regenerate it on a machine "
        "running ComfyUI: python scripts/comfyui_object_info_snapshot.py"
    )


def test_snapshot_records_where_it_came_from():
    meta = wc.load_snapshot()["_meta"]
    assert meta["comfyui_version"] and meta["captured"] and meta["missing"] == []


def test_snapshot_keeps_no_machine_file_lists():
    script = _snapshot_script()
    for cls, node in wc.object_info().items():
        for group in ("required", "optional"):
            for name, spec in ((node.get("input") or {}).get(group) or {}).items():
                _, options, _ = wc._spec_type_and_opts(spec)
                assert not script._is_file_list(options), f"{cls}.{name} still lists files"


def _graph():
    return wc.builder()._create_wan22_5b_workflow(prompt="p", negative_prompt="n", seed=1, width=832, height=480,
                                                  num_frames=49, fps=24, interpolation_multiplier=1)


def _break(mutate):
    wf = copy.deepcopy(_graph())
    mutate(wf)
    return wc.validate_graph(wf)


def test_a_builder_graph_is_clean():
    assert wc.validate_graph(_graph()) == []


@pytest.mark.parametrize("mutate,expected", [
    (lambda wf: wf["10"]["inputs"].update(model=["99", 0]), "links to missing node '99'"),
    (lambda wf: wf["10"]["inputs"].update(model=["1", 3]), "has no output 3"),
    (lambda wf: wf["10"]["inputs"].update(model=["5", 0]), "expects MODEL"),
    (lambda wf: wf["10"]["inputs"].update(sampler_name="euler_fast"), "is not one of"),
    (lambda wf: wf["10"]["inputs"].update(cfg=-1.0), "below min"),
    (lambda wf: wf["10"]["inputs"].update(steps=20.5), "non-integral"),
    (lambda wf: wf["10"]["inputs"].update(steps="20"), "INT expected"),
    (lambda wf: wf["10"]["inputs"].pop("seed"), "required input 'seed' is missing"),
    (lambda wf: wf["10"]["inputs"].update(sead=1), "not an input of KSampler"),
    (lambda wf: wf["10"].update(class_type="KSamplerTurbo"), "not in the /object_info snapshot"),
    (lambda wf: wf["13"]["inputs"].update(crf="high"), "INT expected"),
    (lambda wf: wf["8"]["inputs"].update(model=["10", 0]), "expects MODEL"),
    (lambda wf: wf.pop("13"), "no VHS_VideoCombine"),
])
def test_each_break_is_reported(mutate, expected):
    errors = _break(mutate)
    assert any(expected in e for e in errors), errors


def test_a_cycle_is_reported():
    def loop(wf):
        wf["1"]["inputs"]["unet_name"] = ["8", 0]
    assert any("dropdown fed by a link" in e for e in _break(loop))
    wf = copy.deepcopy(_graph())
    wf["8"]["inputs"]["model"] = ["8", 0]
    assert any("cycle" in e for e in wc.validate_graph(wf))


def test_autogrow_inputs_are_checked_against_their_template():
    wf = wc.builder()._create_minimax_ref_workflow(prompt="p", ref_images=[f"r{i}.png" for i in range(9)])
    assert wc.validate_graph(wf) == []
    node = next(n for n in wf.values() if n["class_type"] == "MiniMaxH3ReferenceToVideo")
    node["inputs"]["ref_images.ref_image_9"] = node["inputs"]["ref_images.ref_image_0"]
    node["inputs"]["ref_images.picture_0"] = node["inputs"]["ref_images.ref_image_0"]
    errors = wc.validate_graph(wf)
    assert any("past the group's max" in e for e in errors)
    assert any("does not match ref_image_<n>" in e for e in errors)


def test_fake_comfyui_serves_the_snapshot_and_records_the_prompt():
    fake = wc.FakeComfyUI()
    assert fake.get("http://comfy/object_info").json() is fake.info
    assert fake.post("http://comfy/prompt", json={"prompt": {"1": {}}}).json()["prompt_id"] == "contract-1"
    assert fake.workflow == {"1": {}}
