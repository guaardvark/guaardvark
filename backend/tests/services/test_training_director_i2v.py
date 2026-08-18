#!/usr/bin/env python3
"""Contract between the training director's I2V call and the batch-video API.

`visuals.animate` falls back to the still on any dispatch failure, by design —
motion must never block a production. That silence hides a malformed request
indefinitely, so the request shape is asserted here rather than discovered in a
render.
"""

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
os.environ["GUAARDVARK_MODE"] = "test"


def _load_director_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The director's modules import their siblings by bare name, and `config` is
# already taken by backend/config.py once anything else has imported it. Load
# them from their paths and hand the name back, so import order cannot decide
# which `config` visuals binds to.
_TD = REPO / "scripts" / "training_director"
_displaced = sys.modules.get("config")
td_config = _load_director_module("config", _TD / "config.py")
visuals = _load_director_module("visuals", _TD / "visuals.py")
if _displaced is not None:
    sys.modules["config"] = _displaced
else:
    sys.modules.pop("config", None)

VIDEO_FPS = td_config.VIDEO_FPS
VIDEO_FRAMES = td_config.VIDEO_FRAMES
VIDEO_MODEL = td_config.VIDEO_MODEL

from backend.services.video_model_registry import (  # noqa: E402
    VIDEO_MODEL_REGISTRY,
)


class TestI2VPayloadShape(unittest.TestCase):
    """Mirrors backend/api/batch_video_generation_api.py:generate_image_to_video_batch."""

    def setUp(self):
        self.payload = visuals.i2v_payload("/tmp/s00_0.png", "a slate roof at dawn")

    def test_sends_image_paths_as_a_list(self):
        # The endpoint 400s on anything else: it reads data["image_paths"].
        self.assertIsInstance(self.payload["image_paths"], list)
        self.assertEqual(self.payload["image_paths"], ["/tmp/s00_0.png"])

    def test_sends_frame_count_and_fps_not_seconds(self):
        self.assertEqual(self.payload["duration_frames"], VIDEO_FRAMES)
        self.assertEqual(self.payload["fps"], VIDEO_FPS)
        self.assertNotIn("duration", self.payload)

    def test_carries_the_series_style_and_negative_prompt(self):
        self.assertIn(visuals.STYLE_SUFFIX, self.payload["prompt"])
        self.assertEqual(self.payload["negative_prompt"], visuals.NEGATIVE)

    def test_prompt_enhancement_is_off(self):
        # The enhancer reintroduces the countable detail the prompt discipline
        # in visuals.py deliberately excludes.
        self.assertIs(self.payload["enhance_prompt"], False)

    def test_requests_the_project_frame_size(self):
        self.assertGreater(self.payload["width"], 0)
        self.assertGreater(self.payload["height"], 0)


class TestConfiguredModel(unittest.TestCase):

    def test_default_model_exists_in_the_registry(self):
        # preflight_video_model rejects an unknown id, so a plausible-looking
        # name that is not a registry key disables motion silently.
        self.assertIn(VIDEO_MODEL, VIDEO_MODEL_REGISTRY)

    def test_default_model_has_an_i2v_branch_in_the_dispatcher(self):
        # comfyui_video_generator routes I2V by explicit per-model branches, so a
        # T2V-only id would dispatch and then render without the start frame.
        self.assertIn(VIDEO_MODEL, {
            "wan22-14b-i2v", "wan22-5b", "cogvideox-5b-i2v",
            "ltx23-distilled-fp8", "ltx25-distilled-int8",
        })

    def test_payload_dimensions_suit_the_model_budget(self):
        entry = VIDEO_MODEL_REGISTRY[VIDEO_MODEL]
        # Oversize requests are clamped and aligned server-side; assert the
        # metadata the clamp depends on is actually declared for this model.
        self.assertGreater(entry["max_pixel_area"], 0)
        self.assertEqual(entry["dimension_alignment"] % 8, 0)


class TestPeopleExclusion(unittest.TestCase):
    """Figures are excluded unless a shot asks for one."""

    def test_default_excludes_people(self):
        neg = visuals.negative_for(False)
        for term in ("person", "people", "face"):
            self.assertIn(term, neg)

    def test_a_shot_can_admit_people(self):
        neg = visuals.negative_for(True)
        self.assertNotIn("person", neg)
        # the rest of the exclusions survive
        self.assertIn("watermark", neg)
        self.assertIn("cartoon", neg)

    def test_both_forms_keep_the_base_exclusions(self):
        for people in (True, False):
            self.assertTrue(visuals.negative_for(people).startswith(visuals.NEGATIVE))


class TestRenderedClipLookup(unittest.TestCase):
    """Mirrors a settled batch's status payload.

    The batch reports its clip under `frame_paths`, relative to `output_dir`.
    Reading only `video_path` finds nothing and the shot silently keeps the
    still even though the render succeeded.
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clip = self.root / "item-1" / "videos" / "wan22_5b_00031.mp4"
        self.clip.parent.mkdir(parents=True)
        self.clip.write_bytes(b"\x00")

    def _payload(self, **result):
        return {"output_dir": str(self.root), "results": [result]}

    def test_relative_frame_paths_resolve_against_output_dir(self):
        data = self._payload(
            frame_paths=["item-1/videos/wan22_5b_00031.mp4"])
        self.assertEqual(visuals._rendered_clip(data), self.clip)

    def test_absolute_video_path_still_works(self):
        data = self._payload(video_path=str(self.clip))
        self.assertEqual(visuals._rendered_clip(data), self.clip)

    def test_a_missing_file_is_not_returned(self):
        data = self._payload(frame_paths=["item-1/videos/gone.mp4"])
        self.assertIsNone(visuals._rendered_clip(data))

    def test_non_video_artifacts_are_skipped(self):
        thumb = self.root / "item-1" / "t.jpg"
        thumb.write_bytes(b"\x00")
        data = self._payload(frame_paths=["item-1/t.jpg",
                                          "item-1/videos/wan22_5b_00031.mp4"])
        self.assertEqual(visuals._rendered_clip(data), self.clip)

    def test_empty_results(self):
        self.assertIsNone(visuals._rendered_clip({"output_dir": str(self.root),
                                                  "results": []}))


class TestAnimateNeverBlocksAProduction(unittest.TestCase):
    """A failed I2V render must degrade to the still, never raise."""

    def setUp(self):
        self.still = Path("/tmp/s01_0.png")
        self.dest = Path("/tmp/s01_motion.mp4")

    def test_a_hard_failure_returns_the_still(self):
        with mock.patch.object(visuals, "release_voice_vram"), \
             mock.patch.object(visuals, "_dispatch_i2v",
                               side_effect=RuntimeError("HTTP 400: unknown model")):
            self.assertEqual(visuals.animate(self.still, "a dome", self.dest),
                             self.still)

    def test_a_headroom_failure_escalates_then_gives_up(self):
        err = RuntimeError("batch failed: CUDA out of memory")
        with mock.patch.object(visuals, "release_voice_vram"), \
             mock.patch.object(visuals, "_free_vram") as freed, \
             mock.patch.object(visuals.time, "sleep"), \
             mock.patch.object(visuals, "_dispatch_i2v", side_effect=err) as sent:
            self.assertEqual(visuals.animate(self.still, "a dome", self.dest),
                             self.still)
        self.assertEqual(sent.call_count, visuals.HEADROOM_RETRIES)
        # Recovery runs between attempts, not after the last one.
        self.assertEqual(freed.call_count, visuals.HEADROOM_RETRIES - 1)

    def test_the_narrator_is_evicted_before_dispatch(self):
        # The MoE holds two experts; the voice model's context alone can deny it
        # headroom on a 16GB card.
        with mock.patch.object(visuals, "release_voice_vram") as evicted, \
             mock.patch.object(visuals, "_dispatch_i2v", return_value="b1"), \
             mock.patch.object(visuals, "_collect_i2v", return_value=self.dest):
            visuals.animate(self.still, "a dome", self.dest)
        evicted.assert_called_once()


if __name__ == "__main__":
    unittest.main()
