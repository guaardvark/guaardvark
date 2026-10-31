"""The Image Edit page's pieces: several PuLID references in one graph, references
batched without squashing, the page naming only its own pictures, and facexlib's
weights checked in every folder that loads them."""
import os

import pytest

try:
    from PIL import Image
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.services import face_reference
    from backend.services import video_model_registry as registry
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def test_one_reference_keeps_the_single_load_node():
    wf = ComfyUIImageGenerator()._build_pulid_workflow(
        src_image_names=["a.png"], prompt="a man in a diner", width=768, height=1024, steps=20, seed=1,
    )
    assert wf["load"]["inputs"]["image"] == "a.png"
    assert "references" not in wf
    assert wf["apply"]["inputs"]["image"] == ["load", 0]


def test_several_references_go_to_the_apply_node_as_one_batch():
    wf = ComfyUIImageGenerator()._build_pulid_workflow(
        src_image_names=["a.png", "b.png", "c.png"], prompt="a man in a diner",
        width=768, height=1024, steps=20, seed=1,
    )
    assert [wf[f"load_{i}"]["inputs"]["image"] for i in range(3)] == ["a.png", "b.png", "c.png"]
    batch = wf["references"]
    assert batch["class_type"] == "BatchImagesNode"
    assert batch["inputs"] == {
        "images.image0": ["load_0", 0], "images.image1": ["load_1", 0], "images.image2": ["load_2", 0],
    }
    assert wf["apply"]["inputs"]["image"] == ["references", 0]


def test_a_graph_needs_a_reference():
    with pytest.raises(ValueError):
        ComfyUIImageGenerator()._build_pulid_workflow(prompt="x", width=768, height=1024, steps=20, seed=1)


def test_batched_references_share_one_square_size(tmp_path):
    tall = tmp_path / "tall.png"
    wide = tmp_path / "wide.png"
    Image.new("RGB", (600, 1500), "red").save(tall)
    Image.new("RGB", (900, 500), "blue").save(wide)
    out = face_reference.batch_references([str(tall), str(wide)], str(tmp_path / "out"))
    sizes = {Image.open(p).size for p in out}
    assert sizes == {(face_reference.MAX_SIDE, face_reference.MAX_SIDE)}
    # A single reference is sent as it is.
    assert face_reference.batch_references([str(tall)], str(tmp_path / "out")) == [str(tall)]


def test_the_page_names_only_its_own_two_folders(tmp_path, monkeypatch):
    from backend.api import image_edit_api as api
    monkeypatch.setattr(api, "_outputs_dir", lambda: str(tmp_path))
    (tmp_path / "edit_inputs").mkdir()
    (tmp_path / "edit_inputs" / "page_src_x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "secret.txt").write_text("no")
    assert api._resolve("/api/outputs/edit_inputs/page_src_x.png") == os.path.join(
        str(tmp_path), "edit_inputs", "page_src_x.png")
    for ref in ("/api/outputs/edit_inputs/../secret.txt", "/api/outputs/secret.txt",
                "/etc/passwd", "/api/outputs/edit_inputs/missing.png", None, 3):
        assert api._resolve(ref) is None


def test_facexlib_files_count_only_when_every_loader_has_them(tmp_path, monkeypatch):
    comfy, backend = tmp_path / "comfy", tmp_path / "backend"
    for folder in (comfy, backend):
        folder.mkdir()
    monkeypatch.setattr(registry, "facexlib_weight_dirs", lambda: [comfy, backend])
    entry = registry.VIDEO_MODEL_REGISTRY["facexlib-face"]
    assert "parsing_bisenet.pth" in entry["check_files"]
    assert registry.resolve_entry_dir(entry) == comfy
    for name in entry["check_files"]:
        (backend / name).write_bytes(b"x")
    assert not registry.entry_files_present(entry)
    for name in entry["check_files"]:
        (comfy / name).write_bytes(b"x")
    assert registry.entry_files_present(entry)
