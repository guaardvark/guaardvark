"""PuLID likeness experiment: switchable builder inputs and the matrix runner.

The builder's overrides (weight, start_at, end_at, unet_dtype, node_variant)
land in the right node inputs; the defaults are unchanged; pulid_classic is
refused with the reason; the runner's dry run prints one graph per case and
writes the index without submitting anything.
"""

import importlib.util
import json
import os

import pytest

import backend.services.comfyui_image_generator as cig
from backend.services.comfyui_image_generator import ComfyUIImageGenerator

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RUNNER = os.path.join(ROOT, "scripts", "experiments", "pulid_matrix.py")


def _build(**overrides):
    return ComfyUIImageGenerator()._build_pulid_workflow(
        src_image_name="face.png", prompt="a 1940s detective in the rain",
        width=768, height=1024, steps=20, seed=1, **overrides,
    )


# ── builder ────────────────────────────────────────────────────────────────

def test_defaults_come_from_the_registry_entry():
    wf = _build()
    assert wf["unet"]["inputs"]["weight_dtype"] == cig.FLUX_DEV_WEIGHT_DTYPE
    assert wf["apply"]["class_type"] == "ApplyPulidFlux"
    # The defaults are declared on the pulid-flux registry entry with the
    # measurement behind them (2026-09-19 matrix); the graph must carry those.
    assert wf["apply"]["inputs"]["weight"] == cig.PULID_IDENTITY_DEFAULTS["weight"]
    assert wf["apply"]["inputs"]["start_at"] == cig.PULID_IDENTITY_DEFAULTS["start_at"]
    assert wf["apply"]["inputs"]["end_at"] == cig.PULID_IDENTITY_DEFAULTS["end_at"]
    assert cig.PULID_IDENTITY_DEFAULTS == {"weight": 1.0, "start_at": 0.2, "end_at": 1.0}


def test_overrides_land_in_the_apply_and_unet_nodes():
    wf = _build(weight=1.5, start_at=0.2, end_at=0.9, unet_dtype="fp8_e4m3fn", node_variant="pulid_flux")
    assert wf["apply"]["inputs"]["weight"] == 1.5
    assert wf["apply"]["inputs"]["start_at"] == 0.2
    assert wf["apply"]["inputs"]["end_at"] == 0.9
    assert wf["unet"]["inputs"]["weight_dtype"] == "fp8_e4m3fn"
    # Everything else is the product graph.
    assert wf["sampler"]["inputs"]["model"] == ["apply", 0]
    assert wf["apply"]["inputs"]["model"] == ["unet", 0]


def test_bf16_means_the_loader_default_dtype():
    """UNETLoader has no bf16 choice; 'default' loads flux1-dev as stored (BF16)."""
    assert _build(unet_dtype="bf16")["unet"]["inputs"]["weight_dtype"] == "default"
    assert _build(unet_dtype="default")["unet"]["inputs"]["weight_dtype"] == "default"
    assert "bf16" not in set(cig.PULID_UNET_DTYPES.values())


def test_unknown_dtype_and_variant_are_refused():
    with pytest.raises(ValueError, match="unet_dtype"):
        _build(unet_dtype="int4")
    with pytest.raises(ValueError, match="node_variant"):
        _build(node_variant="pulid_nextgen")


def test_pulid_classic_is_refused_with_the_reason():
    entry = cig.PULID_NODE_VARIANTS["pulid_classic"]
    assert entry["supported"] is False
    assert "attn2" in entry["reason"]
    with pytest.raises(ValueError, match="attn2"):
        _build(node_variant="pulid_classic")


def test_start_end_window_is_validated():
    with pytest.raises(ValueError):
        _build(start_at=0.8, end_at=0.2)
    with pytest.raises(ValueError):
        _build(start_at=1.5)


def test_generate_with_identity_passes_the_switches_through(monkeypatch, tmp_path):
    gen = ComfyUIImageGenerator()
    seen = {}
    monkeypatch.setattr(gen, "_available", lambda: True)
    monkeypatch.setattr(gen, "pulid_installed", lambda: True)
    monkeypatch.setattr(gen, "_upload_image_to_comfyui", lambda p: "face.png")

    def _run(workflow, output_path, **kw):
        seen["workflow"] = workflow
        return output_path

    monkeypatch.setattr(gen, "_run_edit_graph", _run)
    ref = tmp_path / "face.png"
    ref.write_bytes(b"png")
    gen.generate_with_identity(
        image_path=str(ref), prompt="a detective", output_path=str(tmp_path / "out.png"),
        weight=1.25, start_at=0.3, end_at=0.8, unet_dtype="bf16",
    )
    apply = seen["workflow"]["apply"]["inputs"]
    assert (apply["weight"], apply["start_at"], apply["end_at"]) == (1.25, 0.3, 0.8)
    assert seen["workflow"]["unet"]["inputs"]["weight_dtype"] == "default"


# ── the identity tool passes them on, only when given ──────────────────────

def test_identity_tool_forwards_only_the_switches_it_was_given(monkeypatch, tmp_path):
    import backend.config as cfg
    import backend.services.consent_records as cr
    from backend.tools.image_tools import GenerateIdentityTool

    monkeypatch.setattr(cfg, "OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr(cr, "_hash_dir", lambda: str(tmp_path / "consent"))
    face = tmp_path / "face.png"
    face.write_bytes(b"\x89PNG\r\n\x1a\n" + b"png-bytes")
    cr.record_consent(str(face), "ui_upload")

    calls = []

    class _Gen:
        def generate_with_identity(self, **kw):
            calls.append(kw)
            open(kw["output_path"], "wb").write(b"png")
            return kw["output_path"]

    monkeypatch.setattr("backend.services.comfyui_image_generator.ComfyUIImageGenerator", _Gen)
    tool = GenerateIdentityTool()
    assert set(tool._PASSTHROUGH) <= set(tool.parameters)

    assert tool.execute(prompt="a detective", image=str(face)).success
    assert not (set(calls[0]) & set(tool._PASSTHROUGH))

    assert tool.execute(prompt="a detective", image=str(face), weight=1.5, start_at=0.2, unet_dtype="bf16").success
    assert calls[1]["weight"] == 1.5
    assert calls[1]["start_at"] == 0.2
    assert calls[1]["unet_dtype"] == "bf16"
    assert "end_at" not in calls[1] and "node_variant" not in calls[1]


# ── the runner ─────────────────────────────────────────────────────────────

def _runner():
    spec = importlib.util.spec_from_file_location("pulid_matrix", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dry_run_prints_one_graph_per_case_and_writes_the_index(tmp_path, capsys):
    mod = _runner()
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"png")
    out = tmp_path / "matrix"
    rc = mod.main([
        "--image", str(ref), "--prompt", "a 1940s detective", "--dry-run", "--out", str(out),
        "--dtypes", "fp8_e4m3fn,bf16", "--weights", "1.0,1.5", "--start-ats", "0.0,0.2",
    ])
    assert rc == 0
    text = capsys.readouterr().out
    headers = [line for line in text.splitlines() if line.startswith("== ")]
    assert len(headers) == 8
    assert '"class_type": "ApplyPulidFlux"' in text
    index = json.loads((out / "matrix.json").read_text())
    assert index["dry_run"] is True
    assert len(index["cases"]) == 8
    names = {c["name"] for c in index["cases"]}
    assert "pulid_flux_bf16_w1.5_s0.2" in names
    assert all(c["status"] == "dry-run" for c in index["cases"])
    assert all(c["output"].endswith(c["name"] + ".png") for c in index["cases"])
    assert not list(out.glob("*.png"))


def test_only_filter_and_unsupported_variant_are_recorded(tmp_path, capsys):
    mod = _runner()
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"png")
    out = tmp_path / "matrix"
    rc = mod.main([
        "--image", str(ref), "--prompt", "a detective", "--dry-run", "--out", str(out),
        "--dtypes", "fp8_e4m3fn,bf16", "--weights", "1.0", "--start-ats", "0.0",
        "--variants", "pulid_flux,pulid_classic", "--only", "bf16",
    ])
    assert rc == 0
    index = json.loads((out / "matrix.json").read_text())
    by_name = {c["name"]: c for c in index["cases"]}
    assert set(by_name) == {"pulid_flux_bf16_w1_s0", "pulid_classic_bf16_w1_s0"}
    assert by_name["pulid_flux_bf16_w1_s0"]["status"] == "dry-run"
    classic = by_name["pulid_classic_bf16_w1_s0"]
    assert classic["status"] == "unsupported"
    assert "attn2" in classic["error"]
    assert "unsupported" in capsys.readouterr().out


def test_missing_reference_is_an_error(tmp_path, capsys):
    mod = _runner()
    rc = mod.main(["--image", str(tmp_path / "nope.png"), "--prompt", "x", "--dry-run", "--out", str(tmp_path)])
    assert rc == 2
