"""SVD graph contract. SVD is retired: no registry entry, no route in
generate_video, and _create_svd_workflow has no caller. The builder is kept
and checked so its state is on record (docs/video-pipeline.md §8)."""
import pytest

from backend.tests.fixtures import workflow_contract as wc


def test_svd_classes_are_served():
    wf = wc.builder()._create_svd_workflow("start.png", num_frames=25, fps=7, seed=7)
    assert wc.class_types(wf) <= set(wc.object_info())


@pytest.mark.xfail(strict=True, reason=(
    "unreachable builder: the KSampler reads positive/negative from EmptyLatentImage and its "
    "latent from SVD_img2vid_Conditioning output 0 (a CONDITIONING), and VHS_VideoCombine "
    "lacks the required pingpong/save_output"))
def test_svd_builder_graph_is_valid():
    wc.assert_valid(wc.builder()._create_svd_workflow("start.png", num_frames=25, fps=7, seed=7))


def test_svd_is_not_routed():
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY

    assert not any("svd" in mid for mid in VIDEO_MODEL_REGISTRY)
