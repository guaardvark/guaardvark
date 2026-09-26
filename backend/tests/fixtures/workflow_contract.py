"""Contract checks for the ComfyUI graphs the video builders emit.

``validate_graph`` checks a workflow in ComfyUI's API format against the
checked-in ``/object_info`` snapshot (``comfyui_object_info.json``, regenerate
with ``scripts/comfyui_object_info_snapshot.py``). It follows ComfyUI's own
prompt validation (``execution.validate_inputs``): every class exists, every
required input is given, links point at an existing node and a real output
slot of a compatible type, numbers sit inside min/max and dropdown values are
among the options. It is stricter in two places ComfyUI stays silent: an input
name the class does not declare (ComfyUI drops it without a word) and a
non-integral float sent to an INT (ComfyUI truncates it).

``FakeComfyUI`` stands in for the HTTP server at the seam ``generate_video``
calls (``requests`` in comfyui_video_generator), so the graph a real request
produces can be captured after every clamp and optional node has run.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from urllib.parse import urlsplit

import pytest

SNAPSHOT_PATH = Path(__file__).with_name("comfyui_object_info.json")

_LINK_TYPES_ANY = {"*", "COMFY_ANY"}

# Keys the builders send that no class declares. ComfyUI ignores both; they
# are UI widget state copied from exported workflows, so they are tolerated
# rather than reported as a misspelt input.
IGNORED_INPUTS = {
    "*": {"control_after_generate"},
    "VHS_VideoCombine": {"videopreview"},
}


@functools.lru_cache(maxsize=1)
def load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def object_info() -> dict:
    return load_snapshot()["nodes"]


def _is_link(value) -> bool:
    return (
        isinstance(value, list) and len(value) == 2
        and isinstance(value[0], str) and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def _spec_type_and_opts(spec):
    """(type, options) for an input spec. type is a string; options is the
    dropdown list for a combo (possibly empty: a model-file list), else None."""
    head = spec[0] if isinstance(spec, list) and spec else spec
    extra = spec[1] if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(head, list):
        return "COMBO", head, extra
    if head == "COMBO":
        return "COMBO", list(extra.get("options") or []), extra
    return str(head), None, extra


def _types_compatible(received: str, expected: str) -> bool:
    got = {t.strip() for t in str(received).split(",")}
    want = {t.strip() for t in str(expected).split(",")}
    if got & _LINK_TYPES_ANY or want & _LINK_TYPES_ANY:
        return True
    return bool(got & want)


def _check_value(where: str, value, type_name: str, options, extra: dict) -> list:
    if "," in type_name:
        # A multi-type input (``FLOAT,INT``) takes a literal of any of its types.
        attempts = [_check_value(where, value, t.strip(), options, extra) for t in type_name.split(",")]
        return [] if any(not a for a in attempts) else attempts[0]
    errors = []
    if type_name == "COMBO":
        if options and value not in options:
            errors.append(f"{where}: {value!r} is not one of {options[:12]}{'…' if len(options) > 12 else ''}")
        elif not options and not isinstance(value, str):
            errors.append(f"{where}: file-list value must be a string, got {value!r}")
        return errors
    if type_name == "INT":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{where}: INT expected, got {value!r}"]
        if isinstance(value, float) and not value.is_integer():
            errors.append(f"{where}: INT got non-integral {value!r} (ComfyUI truncates it)")
    elif type_name == "FLOAT":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{where}: FLOAT expected, got {value!r}"]
    elif type_name == "STRING":
        if not isinstance(value, str):
            return [f"{where}: STRING expected, got {value!r}"]
    elif type_name == "BOOLEAN":
        if not isinstance(value, bool):
            return [f"{where}: BOOLEAN expected, got {value!r}"]
    else:
        return [f"{where}: {type_name} must be a link, got the literal {value!r}"]
    if type_name in ("INT", "FLOAT"):
        lo, hi = extra.get("min"), extra.get("max")
        if lo is not None and value < lo:
            errors.append(f"{where}: {value} is below min {lo}")
        if hi is not None and value > hi:
            errors.append(f"{where}: {value} is above max {hi}")
    return errors


def _declared_inputs(node_info: dict) -> dict:
    inputs = node_info.get("input") or {}
    declared = {}
    for group in ("required", "optional"):
        for name, spec in (inputs.get(group) or {}).items():
            declared[name] = (group, spec)
    return declared


def _autogrow_spec(declared: dict, name: str):
    """Spec of a dotted autogrow input (``ref_images.ref_image_0``), else None."""
    if "." not in name:
        return None, None
    parent, child = name.split(".", 1)
    group_spec = declared.get(parent)
    if not group_spec:
        return None, f"no autogrow group {parent!r}"
    _, spec = group_spec
    if not (isinstance(spec, list) and spec and spec[0] == "COMFY_AUTOGROW_V3"):
        return None, f"{parent!r} is not an autogrow group"
    grow = (spec[1] or {}).get("template") or {}
    prefix = grow.get("prefix") or ""
    if not child.startswith(prefix) or not child[len(prefix):].isdigit():
        return None, f"{child!r} does not match {prefix}<n>"
    index = int(child[len(prefix):])
    if index >= int(grow.get("max") or 0):
        return None, f"{child!r} is past the group's max of {grow.get('max')}"
    template = ((grow.get("input") or {}).get("required") or {})
    return next(iter(template.values()), None), None


def _vhs_format_widgets(node_info: dict, fmt) -> dict:
    """Extra widgets VHS_VideoCombine accepts for the chosen format."""
    spec = ((node_info.get("input") or {}).get("required") or {}).get("format")
    if not spec or len(spec) < 2:
        return {}
    widgets = {}
    for entry in (spec[1].get("formats") or {}).get(fmt, []):
        name, type_or_opts = entry[0], entry[1]
        extra = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
        widgets[name] = [type_or_opts, extra]
    return widgets


def validate_graph(workflow: dict, info: Optional[dict] = None) -> list:
    """Every contract violation in ``workflow`` as a readable line; [] when clean."""
    info = info if info is not None else object_info()
    errors = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or "class_type" not in node:
            errors.append(f"node {node_id}: not a node dict")
            continue
        cls = node["class_type"]
        node_info = info.get(cls)
        if node_info is None:
            errors.append(f"node {node_id}: class {cls!r} is not in the /object_info snapshot")
            continue
        inputs = node.get("inputs") or {}
        declared = _declared_inputs(node_info)
        extra_widgets = _vhs_format_widgets(node_info, inputs.get("format")) if cls == "VHS_VideoCombine" else {}
        for name, (group, spec) in declared.items():
            head = spec[0] if isinstance(spec, list) and spec else spec
            if group == "required" and name not in inputs and head != "COMFY_AUTOGROW_V3":
                errors.append(f"node {node_id} ({cls}): required input {name!r} is missing")
        ignored = IGNORED_INPUTS["*"] | IGNORED_INPUTS.get(cls, set())
        for name, value in inputs.items():
            where = f"node {node_id} ({cls}).{name}"
            if name in ignored and name not in declared:
                continue
            if name in declared:
                spec = declared[name][1]
            elif name in extra_widgets:
                spec = extra_widgets[name]
            else:
                spec, why = _autogrow_spec(declared, name)
                if spec is None:
                    errors.append(f"{where}: not an input of {cls}" + (f" ({why})" if why else ""))
                    continue
            type_name, options, extra = _spec_type_and_opts(spec)
            if _is_link(value):
                src_id, slot = value
                src = workflow.get(src_id)
                if not isinstance(src, dict):
                    errors.append(f"{where}: links to missing node {src_id!r}")
                    continue
                src_info = info.get(src.get("class_type"))
                if src_info is None:
                    continue  # reported on the source node itself
                outputs = src_info.get("output") or []
                if not 0 <= slot < len(outputs):
                    errors.append(f"{where}: node {src_id} ({src['class_type']}) has no output {slot}")
                    continue
                if type_name == "COMBO":
                    errors.append(f"{where}: a dropdown fed by a link from node {src_id}")
                elif not _types_compatible(outputs[slot], type_name):
                    errors.append(
                        f"{where}: expects {type_name}, node {src_id} ({src['class_type']}) "
                        f"output {slot} is {outputs[slot]}"
                    )
            else:
                errors.extend(_check_value(where, value, type_name, options, extra))
    errors.extend(_cycle_errors(workflow))
    if not any(isinstance(n, dict) and n.get("class_type") == "VHS_VideoCombine" for n in workflow.values()):
        errors.append("graph has no VHS_VideoCombine output node")
    return errors


def _cycle_errors(workflow: dict) -> list:
    state: dict = {}

    def visit(nid) -> bool:
        if state.get(nid) == 1:
            return True
        if state.get(nid) == 2:
            return False
        state[nid] = 1
        node = workflow.get(nid) or {}
        for value in (node.get("inputs") or {}).values():
            if _is_link(value) and value[0] in workflow and visit(value[0]):
                return True
        state[nid] = 2
        return False

    return [f"graph has a cycle through node {nid}" for nid in list(workflow) if visit(nid)][:1]


def assert_valid(workflow: dict) -> None:
    errors = validate_graph(workflow)
    assert not errors, "graph breaks the ComfyUI contract:\n  " + "\n  ".join(errors)


# ── Graph queries ────────────────────────────────────────────────────────────

def nodes(workflow: dict, class_type: str) -> list:
    """[(node_id, node)] of one class, in numeric id order."""
    found = [(nid, n) for nid, n in workflow.items() if isinstance(n, dict) and n.get("class_type") == class_type]
    return sorted(found, key=lambda item: (not str(item[0]).isdigit(), int(item[0]) if str(item[0]).isdigit() else 0, item[0]))


def one(workflow: dict, class_type: str):
    found = nodes(workflow, class_type)
    assert len(found) == 1, f"expected one {class_type}, found {len(found)}"
    return found[0]


def class_types(workflow: dict) -> set:
    return {n["class_type"] for n in workflow.values() if isinstance(n, dict) and "class_type" in n}


def source(workflow: dict, ref) -> dict:
    """The node a link points at."""
    assert _is_link(ref), f"{ref!r} is not a link"
    return workflow[ref[0]]


def video_output(workflow: dict):
    return one(workflow, "VHS_VideoCombine")


def output_fps(workflow: dict) -> float:
    return video_output(workflow)[1]["inputs"]["frame_rate"]


def frames_feeding_video(workflow: dict) -> dict:
    """The node whose frames VHS_VideoCombine writes."""
    return source(workflow, video_output(workflow)[1]["inputs"]["images"])


def orphan_nodes(workflow: dict) -> list:
    """Nodes no output depends on. ComfyUI never runs them, so one here means
    a rewiring step left part of the graph behind."""
    outputs = [nid for nid, n in workflow.items() if n.get("class_type") in ("VHS_VideoCombine", "SaveImage")]
    seen, stack = set(), list(outputs)
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in workflow:
            continue
        seen.add(nid)
        stack.extend(v[0] for v in (workflow[nid].get("inputs") or {}).values() if _is_link(v))
    return sorted(set(workflow) - seen)


def trace_conditioning(workflow: dict, ref) -> dict:
    """Follow a conditioning link back to the node that encoded the text.

    A node that passes conditioning through names its output after the input
    it came from (WanImageToVideo ``positive`` → ``positive``); a node with a
    single conditioning input (FluxGuidance) is followed through it."""
    info = object_info()
    for _ in range(32):
        node = source(workflow, ref)
        node_info = info.get(node["class_type"]) or {}
        inputs = node.get("inputs") or {}
        if any(k in inputs and isinstance(inputs[k], str) for k in ("text", "prompt")):
            return node
        names = [str(n).lower() for n in node_info.get("output_name") or []]
        slot_name = names[ref[1]] if ref[1] < len(names) else ""
        nxt = inputs.get(slot_name)
        if not _is_link(nxt):
            cond = [
                v for k, v in inputs.items() if _is_link(v)
                and _spec_type_and_opts(_declared_inputs(node_info).get(k, (None, ["?"]))[1])[0] == "CONDITIONING"
            ]
            nxt = cond[0] if len(cond) == 1 else None
        if not _is_link(nxt):
            raise AssertionError(f"cannot follow conditioning through {node['class_type']}")
        ref = nxt
    raise AssertionError("conditioning chain too long")


def encoded_text(workflow: dict, ref) -> str:
    inputs = trace_conditioning(workflow, ref)["inputs"]
    return inputs.get("text", inputs.get("prompt"))


def model_path_classes(workflow: dict, ref) -> list:
    """Classes on the MODEL chain behind a link, nearest first."""
    out = []
    for _ in range(64):
        node = source(workflow, ref)
        out.append(node["class_type"])
        nxt = (node.get("inputs") or {}).get("model")
        if not _is_link(nxt):
            return out
        ref = nxt
    raise AssertionError("model chain too long")


def canvas(workflow: dict) -> list:
    """(node_id, width, height, frames) for every node that sets the clip's size."""
    found = []
    for nid, node in workflow.items():
        inputs = node.get("inputs") or {}
        w, h = inputs.get("width"), inputs.get("height")
        if isinstance(w, int) and isinstance(h, int) and not isinstance(w, bool):
            found.append((nid, w, h, inputs.get("length", inputs.get("num_frames"))))
    return found


def pixel_cap(model_id: str, frames: int) -> Optional[int]:
    """The largest frame area the registry allows this model at this length."""
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, model_capabilities

    entry = VIDEO_MODEL_REGISTRY.get(model_id) or {}
    cap = model_capabilities(model_id).get("max_pixel_area")
    tiers = sorted((t for t in entry.get("duration_tiers") or [] if t.get("frames")), key=lambda t: t["frames"])
    if tiers:
        tier = next((t for t in tiers if frames <= t["frames"]), tiers[-1])
        if tier.get("max_pixel_area"):
            cap = min(cap or tier["max_pixel_area"], tier["max_pixel_area"])
    return cap


def on_frame_grid(model_id: str, frames: int) -> bool:
    from backend.services.video_model_registry import snap_frames

    return snap_frames(model_id, frames) == frames


RATIOS_WHEN_UNDECLARED = ("16:9", "9:16", "1:1")


def assert_resolved_canvas(model_id: str, request, ratio: str) -> None:
    """The size generate_video resolved obeys the registry: on the declared
    alignment, inside the pixel budget, within 6% of the asked ratio."""
    from backend.services.video_model_registry import model_capabilities

    caps = model_capabilities(model_id)
    align = int(caps.get("dimension_alignment") or 16)
    assert request.width % align == 0 and request.height % align == 0, (
        f"{request.width}x{request.height} is off the {align}px grid {model_id} declares"
    )
    cap = pixel_cap(model_id, request.duration_frames)
    if cap:
        assert request.width * request.height <= cap, f"{request.width}x{request.height} is over {cap} px"
    w_part, h_part = (float(x) for x in ratio.split(":"))
    assert abs((request.width / request.height) / (w_part / h_part) - 1.0) <= 0.06


def optional_features_render(comfy, monkeypatch, model_id: str, **fields):
    """Render with every optional post node on: RIFE ×2, upscale, face restore
    (nodes served and CodeFormer installed) and frame export."""
    from backend.services import video_model_registry

    real = video_model_registry.is_model_installed
    monkeypatch.setattr(video_model_registry, "is_model_installed",
                        lambda mid: True if mid == "codeformer" else real(mid))
    metadata = {"upscale": True, **fields.pop("metadata", {})}
    return comfy.render(model=model_id, interpolation_multiplier=2, face_restore=True,
                        generate_frames_only=True, metadata=metadata, **fields)


def assert_optional_features(workflow: dict, fps: float) -> None:
    """RIFE → upscale → face restore → VHS, with the PNG export tapping the
    same frames the MP4 gets."""
    assert_valid(workflow)
    assert not orphan_nodes(workflow), orphan_nodes(workflow)
    restore = frames_feeding_video(workflow)
    assert restore["class_type"] == "FaceRestoreCFWithModel"
    upscale = source(workflow, restore["inputs"]["image"])
    assert upscale["class_type"] == "ImageUpscaleWithModel"
    assert source(workflow, upscale["inputs"]["upscale_model"])["inputs"]["model_name"] in registry_files("realesrgan-x2")
    assert source(workflow, restore["inputs"]["facerestore_model"])["inputs"]["model_name"] in registry_files("codeformer")
    rife = source(workflow, upscale["inputs"]["image"])
    assert rife["class_type"] == "RIFE VFI" and rife["inputs"]["multiplier"] == 2
    assert output_fps(workflow) == fps * 2
    _, save = one(workflow, "SaveImage")
    assert save["inputs"]["images"] == video_output(workflow)[1]["inputs"]["images"]


def assert_loras_on_every_model_input(workflow: dict, names: list) -> None:
    """Every node that samples from the model reads it through the user LoRAs,
    in order, with the strengths given."""
    samplers = [
        (nid, n) for nid, n in workflow.items()
        if _is_link((n.get("inputs") or {}).get("model"))
        and n["class_type"] not in ("LoraLoaderModelOnly", "ModelAttentionBackend", "ModelSamplingSD3",
                                    "ModelSamplingLTXV")
    ]
    assert samplers
    for _, node in samplers:
        chain = model_path_classes(workflow, node["inputs"]["model"])
        assert chain.count("LoraLoaderModelOnly") >= len(names), chain


def lora_strengths(workflow: dict) -> dict:
    return {n["inputs"]["lora_name"]: n["inputs"]["strength_model"] for _, n in nodes(workflow, "LoraLoaderModelOnly")}


def registry_files(model_id: str) -> set:
    """Every file name the registry installs for a model and its companions."""
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY

    names, stack, seen = set(), [model_id], set()
    while stack:
        mid = stack.pop()
        if mid in seen:
            continue
        seen.add(mid)
        entry = VIDEO_MODEL_REGISTRY.get(mid) or {}
        names.update(f.get("dst") for f in entry.get("files") or [] if f.get("dst"))
        if entry.get("hf_filename"):
            names.add(entry["hf_filename"])
        if not entry.get("files"):
            names.update(Path(f).name for f in entry.get("check_files") or [])
        stack.extend(entry.get("requires") or [])
    return names


def loader_files(workflow: dict) -> dict:
    """{(node_id, input): file} for every model-file dropdown in the graph."""
    info = object_info()
    found = {}
    for nid, node in workflow.items():
        node_info = info.get(node.get("class_type")) or {}
        declared = _declared_inputs(node_info)
        for name, value in (node.get("inputs") or {}).items():
            if name not in declared or _is_link(value):
                continue
            type_name, options, _ = _spec_type_and_opts(declared[name][1])
            if type_name == "COMBO" and options == [] and isinstance(value, str):
                found[(nid, name)] = value
    return found


# ── ComfyUI at the HTTP seam ─────────────────────────────────────────────────

class _Response:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeComfyUI:
    """What generate_video talks to: /, /object_info, /system_stats,
    /upload/image, /prompt, /history, /queue. /prompt records the graph; the
    history entry then reports an execution error, which ends the wait loop at
    once so no render is ever waited on."""

    STOP = "stopped by the workflow contract test"

    def __init__(self, info: Optional[dict] = None):
        self.info = info if info is not None else object_info()
        self.prompts: list = []
        self.uploads: list = []
        # Scripted replies for the failure tests; the defaults are the above.
        self.alive = True                # False: every request fails to connect
        self.die_after_prompt = False    # ComfyUI goes away once a graph is queued
        self.prompt_reply = None         # (status, body) for POST /prompt
        self.history_entry = None        # the /history entry for a queued prompt
        self.files: dict = {}            # output filename -> bytes served at /view
        self.running = False             # /queue lists the last queued prompt as running
        self.interrupts = []             # prompt ids POSTed to /interrupt (None: unscoped)

    def _connect(self):
        if not self.alive:
            import requests

            raise requests.ConnectionError("connection refused (fake ComfyUI is down)")

    def get(self, url, *args, **kwargs):
        self._connect()
        path = urlsplit(url).path.lstrip("/")
        if not path:
            return _Response(200, {})
        if path.startswith("object_info"):
            return _Response(200, self.info)
        if path.startswith("system_stats"):
            return _Response(200, {"system": {"argv": ["main.py"]}})
        if path.startswith("history/"):
            prompt_id = path.split("/", 1)[1]
            if self.history_entry is not None:
                return _Response(200, {prompt_id: self.history_entry})
            return _Response(200, {prompt_id: {"status": {
                "status_str": "error", "completed": False,
                "messages": [["execution_error", {"exception_message": self.STOP}]],
            }}})
        if path.startswith("queue"):
            running = [[0, f"contract-{len(self.prompts)}"]] if self.running and self.prompts else []
            return _Response(200, {"queue_running": running, "queue_pending": []})
        return _Response(404, {})

    def post(self, url, json=None, files=None, data=None, **kwargs):
        self._connect()
        if url.endswith("/prompt"):
            self.prompts.append(json["prompt"])
            if self.prompt_reply is not None:
                return _Response(*self.prompt_reply)
            if self.die_after_prompt:
                self.alive = False
            return _Response(200, {"prompt_id": f"contract-{len(self.prompts)}"})
        if url.endswith("/interrupt"):
            # ComfyUI stops the sampler and the prompt's history ends in
            # execution_interrupted; the queue no longer lists it as running.
            self.interrupts.append((json or {}).get("prompt_id"))
            if self.prompts:
                self.history_entry = {"status": {"status_str": "error", "completed": False, "messages": [
                    ["execution_interrupted", {"prompt_id": f"contract-{len(self.prompts)}",
                                               "node_id": "10", "node_type": "KSampler", "executed": []}]]}}
            self.running = False
            return _Response(200, {})
        if url.endswith("/queue"):
            return _Response(200, {})
        if url.endswith("/upload/image"):
            handle = (files or {}).get("image")
            name = Path(getattr(handle, "name", "upload.png")).name
            self.uploads.append(name)
            return _Response(200, {"name": name})
        return _Response(404, {})

    @property
    def workflow(self) -> dict:
        assert self.prompts, "generate_video queued no graph"
        return self.prompts[-1]

    def finish_with(self, files: dict) -> None:
        """Report the queued prompt finished, with these output files (name ->
        bytes) on the VHS output node, as ComfyUI's history does."""
        self.files = dict(files)
        self.history_entry = {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {"13": {"gifs": [{"filename": name, "subfolder": "", "type": "output"}
                                        for name in files]}},
        }

    def urlretrieve(self, url, destination):
        """urllib.request.urlretrieve, which _download_file uses for /view."""
        self._connect()
        from urllib.parse import parse_qs

        name = parse_qs(urlsplit(url).query).get("filename", [""])[0]
        if name not in self.files:
            raise OSError(f"HTTP Error 404: {name} not found")
        Path(destination).write_bytes(self.files[name])
        return str(destination), None


def install_fake_comfyui(monkeypatch, tmp_path, *, total_vram_mb: int = 16376, info: Optional[dict] = None):
    """Patch the seams generate_video reads at call time and return
    (generator, fake). The VRAM probe reports ``fake.total_vram_mb``; the
    orchestrator booking accepts; the ComfyUI models tree is not local."""
    import requests as _requests

    from backend.services import comfyui_video_generator as cvg
    from backend.services import gpu_memory_orchestrator, gpu_resource_coordinator

    fake = FakeComfyUI(info)
    fake.total_vram_mb = total_vram_mb

    class _Requests:
        get = staticmethod(fake.get)
        post = staticmethod(fake.post)
        HTTPError = _requests.HTTPError
        exceptions = _requests.exceptions
        RequestException = _requests.RequestException

    class _Orchestrator:
        def request_model(self, *args, **kwargs):
            return None

        def begin_use(self, *args, **kwargs):
            return None

    monkeypatch.setattr(cvg, "requests", _Requests)
    monkeypatch.setattr(cvg.urllib.request, "urlretrieve", fake.urlretrieve)
    monkeypatch.setattr(cvg, "COMFYUI_DIR", "", raising=False)
    monkeypatch.setattr(
        gpu_resource_coordinator, "get_available_vram",
        lambda: {"success": True, "total_mb": fake.total_vram_mb, "available_mb": fake.total_vram_mb - 1024,
                 "free_mb": fake.total_vram_mb - 1024},
    )
    monkeypatch.setattr(gpu_memory_orchestrator, "get_orchestrator", lambda: _Orchestrator())
    monkeypatch.setattr(gpu_memory_orchestrator, "get_orchestrator_if_created", lambda: None)
    monkeypatch.setenv("GUAARDVARK_COMFYUI_WS_PROGRESS", "0")
    monkeypatch.delenv("GUAARDVARK_WAN_CLIP_DEVICE", raising=False)
    monkeypatch.delenv("GUAARDVARK_WAN5B_SAMPLER", raising=False)
    monkeypatch.delenv("GUAARDVARK_COMFYUI_ATTENTION", raising=False)
    monkeypatch.delenv("GUAARDVARK_COMFYUI_RESERVE_VRAM", raising=False)

    gen = cvg.ComfyUIVideoGenerator.__new__(cvg.ComfyUIVideoGenerator)
    gen.comfy_url = "http://comfyui.test"
    gen.service_available = True
    gen._object_info_cache = None
    gen._vram_booking = None
    gen._project_root = tmp_path
    gen.default_output_dir = tmp_path / "Videos"
    gen.cache_dir = tmp_path / "cache"
    return gen, fake


def render(gen, fake, tmp_path, **request_fields):
    """Run generate_video with a request and return (result, workflow, request).

    Prompt enhancement is off unless asked for, so the text that reaches the
    graph is the text given here."""
    from backend.services.comfyui_video_generator import VideoGenerationRequest

    fields = {"enhance_prompt": False, "output_dir": tmp_path / "out", "seed": 1234}
    fields.update(request_fields)
    fields.setdefault("metadata", {})
    fields["metadata"] = {"batch_controlled": True, "item_id": "contract", **fields["metadata"]}
    request = VideoGenerationRequest(**fields)
    before = len(fake.prompts)
    result = gen.generate_video(request)
    workflow = fake.prompts[-1] if len(fake.prompts) > before else None
    return result, workflow, request


@pytest.fixture
def comfy(monkeypatch, tmp_path):
    """generate_video wired to FakeComfyUI; ``comfy.render(**request)``."""
    gen, fake = install_fake_comfyui(monkeypatch, tmp_path)
    ns = SimpleNamespace(gen=gen, fake=fake, tmp=tmp_path, image=still_image(tmp_path))
    ns.render = lambda **fields: render(gen, fake, tmp_path, **fields)
    ns.card_for = lambda model_id: setattr(fake, "total_vram_mb", card_for(model_id))
    return ns


def card_for(model_id: str) -> int:
    """Total VRAM (MB) of the smallest card the model declares it needs, at
    least a 16 GB card as pynvml reports one."""
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY

    need_gb = int((VIDEO_MODEL_REGISTRY.get(model_id) or {}).get("min_vram_gb") or 16)
    return max(16376, need_gb * 1024)


def builder():
    """A generator for calling graph builders directly. No ComfyUI is
    reachable, so launch-dependent nodes (the attention pin) stay out."""
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    gen.service_available = False
    gen._object_info_cache = {}
    gen.comfy_url = "http://comfyui.invalid"
    return gen


def still_image(tmp_path) -> str:
    """A real PNG on disk for image-to-video requests."""
    path = tmp_path / "start.png"
    # 1x1 transparent PNG
    path.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d00000000"
        "49454e44ae426082"
    ))
    return str(path)


def ratio_dims(ratio: str, max_area: int, align: int) -> tuple:
    """The largest size of ``ratio`` inside ``max_area``, on the alignment grid."""
    w_part, h_part = (float(x) for x in ratio.split(":"))
    r = w_part / h_part
    height = int((max_area / r) ** 0.5)
    width = int(height * r)
    width = max(align, (width // align) * align)
    height = max(align, (height // align) * align)
    while width * height > max_area:
        if width >= height:
            width -= align
        else:
            height -= align
    return width, height
