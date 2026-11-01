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


def test_two_people_get_chained_apply_nodes_each_with_its_mask():
    wf = ComfyUIImageGenerator()._build_pulid_workflow(
        people=[{"names": ["a.png"], "weight": 1.0, "mask": "mask_a.png"},
                {"names": ["b1.png", "b2.png"], "weight": 1.0, "mask": "mask_b.png"}],
        prompt="a man and a woman in a convertible", width=1360, height=768, steps=20, seed=1,
    )
    assert wf["apply"]["inputs"]["model"] == ["unet", 0]
    assert wf["apply"]["inputs"]["attn_mask"] == ["mask", 0]
    assert wf["mask"]["inputs"] == {"image": "mask_a.png", "channel": "red"}
    assert wf["apply_2"]["inputs"]["model"] == ["apply", 0]
    assert wf["apply_2"]["inputs"]["image"] == ["p2_references", 0]
    assert wf["apply_2"]["inputs"]["attn_mask"] == ["p2_mask", 0]
    assert wf["apply"]["inputs"]["unique_id"] != wf["apply_2"]["inputs"]["unique_id"]
    assert wf["sampler"]["inputs"]["model"] == ["apply_2", 0]


def test_the_scene_render_keeps_every_identity_at_weight_zero():
    wf = ComfyUIImageGenerator()._build_pulid_workflow(
        people=[{"names": ["a.png"], "weight": 0.0, "mask": None}],
        prompt="x", width=768, height=1024, steps=20, seed=1,
    )
    assert wf["apply"]["inputs"]["weight"] == 0.0
    assert "attn_mask" not in wf["apply"]["inputs"]


def test_face_mask_is_white_on_the_face_and_black_far_from_it(tmp_path):
    path = face_reference.face_mask([600, 200, 700, 330], 1360, 768, str(tmp_path))
    mask = Image.open(path)
    assert mask.size == (1360, 768) and mask.mode == "L"
    assert mask.getpixel((650, 250)) > 200
    assert mask.getpixel((100, 700)) == 0


def test_a_face_is_picked_by_its_number():
    from backend.services.identity_scenes import pick
    faces = [{"box": [1, 1, 2, 2]}, {"box": [5, 5, 6, 6]}]
    assert pick(faces, 2, "face") == [5, 5, 6, 6]
    assert "1 to 2" in pick(faces, 3, "face")
    assert "which one" in pick(faces, None, "face")
    assert pick([{"box": [1, 1, 2, 2]}], None, "face") == [1, 1, 2, 2]
    assert "No face" in pick([], 1, "face")


def test_footer_entries_nest_and_a_failed_result_closes_failed(monkeypatch):
    from backend.services import footer_jobs
    sent = []
    monkeypatch.setattr(footer_jobs, "_emit", lambda pid, pct, msg, status, ptype, extra=None:
                        sent.append((pid, status, msg)))
    monkeypatch.setattr(footer_jobs, "_thread_has_entry", lambda: False)
    with footer_jobs.footer_job("image_generation", "Image Edit: expand") as outer:
        with footer_jobs.ensure("image_generation", "Picture for chat", fallback=True) as inner:
            assert inner is outer
        outer.fail("Image Edit: expand: out of memory")
    assert len({pid for pid, _, _ in sent}) == 1
    assert sent[0][1] == "start" and sent[-1][1] == "error"



def test_likeness_bands_follow_the_measured_floors():
    from backend.services.likeness_score import band
    assert band(0.72) == "strong"
    assert band(0.55) == "strong"
    assert band(0.44) == "partial"
    assert band(0.19) == "not_taken"
    assert band(-0.04) == "not_taken"
    assert band(None) == "no_face"


def test_likeness_is_read_at_the_chosen_face_and_counts_every_face_carrying_it(monkeypatch):
    from backend.services import likeness_score
    measured = {"refs_found": [3], "faces": [
        {"box": [360, 220, 470, 364], "scores": [0.72]},
        {"box": [646, 260, 750, 394], "scores": [0.66]},
    ]}
    monkeypatch.setattr(likeness_score, "_measure", lambda image, people: measured)
    chosen = likeness_score.score_render("x.png", [{"paths": ["a.png"], "box": [646, 258, 754, 394]}])
    assert chosen["people"][0]["score"] == 0.66
    single = likeness_score.score_render("x.png", [{"paths": ["a.png"], "box": None}])
    assert single["people"][0]["score"] == 0.72
    assert single["people"][0]["faces_with_likeness"] == 2
    assert "on 2 people" in likeness_score.describe(single)
    nowhere = likeness_score.score_render("x.png", [{"paths": ["a.png"], "box": [0, 0, 50, 50]}])
    assert nowhere["people"][0]["band"] == "no_face"


def test_no_score_when_it_cannot_be_measured(monkeypatch):
    from backend.services import likeness_score
    monkeypatch.setattr(likeness_score, "_measure", lambda image, people: None)
    assert likeness_score.score_render("x.png", [{"paths": ["a.png"], "box": None}]) is None
    assert likeness_score.describe(None) == ""


def test_a_group_photo_is_cut_to_one_face_at_the_centre(tmp_path):
    photo = tmp_path / "group.png"
    Image.new("RGB", (900, 600), (40, 40, 40)).save(photo)
    out = face_reference.crop_to_face(str(photo), [20, 100, 120, 220], str(tmp_path))
    with Image.open(out) as cropped:
        assert cropped.width == cropped.height == face_reference.CROP_MIN_SIDE
        # The face sat near the left edge; the square stays centred on it, grey past the photo.
        assert cropped.getpixel((2, cropped.height // 2)) == (127, 127, 127)
        assert cropped.getpixel((cropped.width // 2, cropped.height // 2)) == (40, 40, 40)


def test_photos_without_a_readable_face_are_not_reported_as_a_faceless_result(monkeypatch):
    from backend.services import likeness_score
    measured = {"refs_found": [0], "faces": [{"box": [10, 10, 60, 60], "scores": [None]}]}
    monkeypatch.setattr(likeness_score, "_measure", lambda image, people: measured)
    report = likeness_score.score_render("x.png", [{"paths": ["a.png"], "box": None}])
    assert report["people"][0]["band"] == "no_reference_face"
    assert "no face in the photos" in likeness_score.describe(report)
