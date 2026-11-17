#!/usr/bin/env python3
"""SD 1.5 removal + model availability gating (2026-08-07).

Background: `runwayml/stable-diffusion-v1-5` was a hidden fallback. When a requested
model failed to load — gated repo, missing download, OOM — generation silently
continued on SD 1.5 and returned an image. The canvas was never re-clamped, so a
2021-era 512px model rendered at 1024²/2048² and produced garbage that looked like
the *requested* model misbehaving. Observed live with Krea 2 Raw and Turbo.

The rules these tests pin:
  1. SD 1.5 is gone from the catalog and there is no default/fallback model.
  2. A load failure is an error that says why — never a substitution.
  3. A half-downloaded model does not count as downloaded.
  4. Models the user cannot run are filtered out of the picker, with a reason.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.offline_image_generator import OfflineImageGenerator  # noqa: E402
from backend.tests.fixtures.model_files import write_model_file  # noqa: E402


def _gen():
    with patch.object(OfflineImageGenerator, "__init__", OfflineImageGenerator.__init__):
        return OfflineImageGenerator()


class TestSD15IsGone(unittest.TestCase):

    def test_no_default_model_attribute(self):
        self.assertFalse(hasattr(_gen(), "default_model"))

    def test_sd15_not_in_catalog(self):
        models = _gen().available_models
        self.assertNotIn("sd-1.5", models)
        self.assertNotIn(
            "runwayml/stable-diffusion-v1-5",
            models.values(),
            "SD 1.5 must not be reachable under any catalog key",
        )

    def test_no_hidden_models_remain(self):
        self.assertEqual(_gen().hidden_models, set())

    def test_auto_router_never_names_sd15(self):
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=False):
            self.assertIsNone(
                g._auto_select_model("a portrait of a person"),
                "with nothing downloaded the router must return None, not a model "
                "that isn't there",
            )

    def test_oom_fallback_never_names_sd15(self):
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=True):
            for failed in ("krea2-turbo", "zimage-turbo", "sd-xl"):
                key = g._oom_fallback_catalog_key(failed)
                self.assertNotEqual(key, "sd-1.5")


class TestDownloadDetection(unittest.TestCase):

    def test_directory_without_model_index_is_not_downloaded(self):
        """The exact Krea 2 failure: an aborted gated download left a README and an
        empty images/ dir, which the old non-empty-directory check called
        'downloaded' — so the menu offered it and every run died in the loader."""
        g = _gen()
        with patch("pathlib.Path.is_file", return_value=False):
            self.assertFalse(g._is_model_downloaded("krea/Krea-2-Turbo"))

    # The "complete tree is downloaded" case needs a real directory, not blanket Path
    # mocks — completeness is now judged from model_index.json's contents. See
    # TestAbortedDownloadDetection.test_all_shards_present_is_downloaded.


class TestSnapshotDownload(unittest.TestCase):
    """Weight-only fetch + resume-on-failure (2026-08-07).

    Krea 2 Turbo's repo is 55 files, 39 of them a model-card gallery and docs that
    diffusers never reads. On a flaky link those sample JPEGs were what actually broke
    the download, and one RemoteProtocolError aborted the whole multi-GB pull.
    """

    def test_gallery_and_docs_are_excluded(self):
        import fnmatch
        pats = OfflineImageGenerator._SNAPSHOT_IGNORE_PATTERNS

        def ignored(f):
            return any(fnmatch.fnmatch(f, p) for p in pats)

        for junk in ("images/12.jpg", "images/00.jpg", "LICENSE.pdf", "README.md", "preview.png"):
            self.assertTrue(ignored(junk), f"{junk} should not be downloaded")

    def test_no_weight_or_config_file_is_excluded(self):
        import fnmatch
        pats = OfflineImageGenerator._SNAPSHOT_IGNORE_PATTERNS

        def ignored(f):
            return any(fnmatch.fnmatch(f, p) for p in pats)

        needed = [
            "model_index.json",
            "scheduler/scheduler_config.json",
            "text_encoder/config.json",
            "text_encoder/model.safetensors",
            "transformer/diffusion_pytorch_model-00001-of-00006.safetensors",
            "vae/diffusion_pytorch_model.safetensors",
            "tokenizer/tokenizer.json",
            "tokenizer/vocab.txt",
            "tokenizer/merges.txt",
            "tokenizer/spiece.model",
        ]
        for f in needed:
            self.assertFalse(ignored(f), f"{f} is required and must be downloaded")

    def test_transient_failure_is_retried(self):
        g = _gen()
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("Server disconnected without sending a response")
            return "/tmp/model"

        with patch("huggingface_hub.snapshot_download", side_effect=flaky), \
             patch("time.sleep"):
            ok, err = g._snapshot_with_retry("krea/Krea-2-Turbo", Path("/tmp/model"))

        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(calls["n"], 3, "should have resumed twice before succeeding")

    def test_gives_up_after_the_attempt_budget(self):
        g = _gen()
        with patch("huggingface_hub.snapshot_download", side_effect=OSError("boom")), \
             patch("time.sleep"), \
             patch.dict(os.environ, {"GUAARDVARK_HF_DOWNLOAD_ATTEMPTS": "2"}):
            ok, err = g._snapshot_with_retry("krea/Krea-2-Turbo", Path("/tmp/model"))

        self.assertFalse(ok)
        self.assertIn("2 attempts", err)
        # Partial progress is reusable, so the message must not imply starting over.
        self.assertIn("resume", err.lower())

    def test_ignore_patterns_are_passed_to_the_hub(self):
        g = _gen()
        with patch("huggingface_hub.snapshot_download", return_value="/tmp/m") as dl, \
             patch.object(g, "_redundant_root_checkpoints", return_value=[]):
            g._snapshot_with_retry("krea/Krea-2-Turbo", Path("/tmp/m"))
        self.assertEqual(
            dl.call_args.kwargs["ignore_patterns"],
            OfflineImageGenerator._SNAPSHOT_IGNORE_PATTERNS,
        )

    def test_redundant_single_file_checkpoint_is_skipped(self):
        """Krea 2 Turbo ships turbo.safetensors (26.28 GB) alongside the transformer/
        shards it duplicates. from_pretrained() never reads it."""
        g = _gen()
        with patch("huggingface_hub.snapshot_download", return_value="/tmp/m") as dl, \
             patch.object(g, "_redundant_root_checkpoints", return_value=["turbo.safetensors"]):
            g._snapshot_with_retry("krea/Krea-2-Turbo", Path("/tmp/m"))
        self.assertIn("turbo.safetensors", dl.call_args.kwargs["ignore_patterns"])


class TestRedundantCheckpointDetection(unittest.TestCase):

    def _files(self, listing):
        api = unittest.mock.MagicMock()
        api.list_repo_files.return_value = listing
        return patch("huggingface_hub.HfApi", return_value=api)

    def test_root_checkpoint_in_a_diffusers_repo_is_redundant(self):
        g = _gen()
        listing = [
            "model_index.json", "turbo.safetensors",
            "transformer/diffusion_pytorch_model-00001-of-00003.safetensors",
            "vae/diffusion_pytorch_model.safetensors",
        ]
        with self._files(listing):
            self.assertEqual(
                g._redundant_root_checkpoints("krea/Krea-2-Turbo"), ["turbo.safetensors"]
            )

    def test_component_weights_are_never_treated_as_redundant(self):
        g = _gen()
        listing = [
            "model_index.json",
            "transformer/diffusion_pytorch_model-00001-of-00003.safetensors",
            "vae/diffusion_pytorch_model.safetensors",
        ]
        with self._files(listing):
            self.assertEqual(g._redundant_root_checkpoints("x/y"), [])

    def test_single_file_repo_keeps_its_only_checkpoint(self):
        """No model_index.json means that root file IS the model — never skip it."""
        g = _gen()
        with self._files(["model.safetensors", "README.md"]):
            self.assertEqual(g._redundant_root_checkpoints("x/y"), [])

    def test_listing_failure_skips_nothing(self):
        g = _gen()
        api = unittest.mock.MagicMock()
        api.list_repo_files.side_effect = OSError("offline")
        with patch("huggingface_hub.HfApi", return_value=api):
            self.assertEqual(g._redundant_root_checkpoints("x/y"), [])


class TestPartialDownloadDetection(unittest.TestCase):
    """model_index.json lands early; it alone must not mean "ready"."""

    def test_incomplete_shards_mean_not_downloaded(self):
        g = _gen()
        with patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.rglob", return_value=iter([Path("x.incomplete")])):
            self.assertFalse(g._is_model_downloaded("krea/Krea-2-Turbo"))

    def test_incomplete_marker_beats_an_otherwise_complete_tree(self):
        """A live download can have every declared weight on disk and still be
        mid-write, so the marker check must not be skippable."""
        import tempfile, json
        g = _gen()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "m"
            (root / "vae").mkdir(parents=True)
            (root / "model_index.json").write_text(json.dumps({
                "vae": ["diffusers", "AutoencoderKL"],
            }))
            write_model_file(root / "vae" / "diffusion_pytorch_model.safetensors")
            with patch.object(g, "_get_model_path", return_value=root):
                self.assertTrue(g._is_model_downloaded("x/y"))

                marker = root / ".cache" / "huggingface" / "download" / "vae"
                marker.mkdir(parents=True)
                (marker / "shard.abc.incomplete").write_text("")
                self.assertFalse(g._is_model_downloaded("x/y"))


class TestAbortedDownloadDetection(unittest.TestCase):
    """A failed download cleans up its own .incomplete markers on the way out.

    Observed 2026-08-07: Krea 2 Turbo died at 14 GB and left a tidy-looking tree —
    model_index.json, every config, the tokenizer and the full VAE — with all three
    transformer shards (~24 GB) absent and no .incomplete files anywhere. Both earlier
    signals said "downloaded".
    """

    def _tree(self, root, *, shards_present):
        import json
        (root / "transformer").mkdir(parents=True)
        (root / "vae").mkdir()
        (root / "scheduler").mkdir()
        (root / "tokenizer").mkdir()
        (root / "model_index.json").write_text(json.dumps({
            "_class_name": "Krea2Pipeline",
            "scheduler": ["diffusers", "FlowMatchEulerDiscreteScheduler"],
            "tokenizer": ["transformers", "Qwen2Tokenizer"],
            "transformer": ["diffusers", "Krea2Transformer2DModel"],
            "vae": ["diffusers", "AutoencoderKL"],
        }))
        (root / "scheduler" / "scheduler_config.json").write_text("{}")
        (root / "tokenizer" / "tokenizer.json").write_text("{}")
        write_model_file(root / "vae" / "diffusion_pytorch_model.safetensors")
        names = [f"diffusion_pytorch_model-0000{i}-of-00003.safetensors" for i in (1, 2, 3)]
        (root / "transformer" / "diffusion_pytorch_model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {f"layer.{i}": n for i, n in enumerate(names)}})
        )
        if shards_present:
            for n in names:
                write_model_file(root / "transformer" / n)

    def test_missing_shards_are_not_downloaded(self):
        import tempfile
        g = _gen()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "krea--Krea-2-Turbo"
            self._tree(root, shards_present=False)
            with patch.object(g, "_get_model_path", return_value=root):
                self.assertFalse(g._is_model_downloaded("krea/Krea-2-Turbo"))

    def test_all_shards_present_is_downloaded(self):
        import tempfile
        g = _gen()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "krea--Krea-2-Turbo"
            self._tree(root, shards_present=True)
            with patch.object(g, "_get_model_path", return_value=root):
                self.assertTrue(g._is_model_downloaded("krea/Krea-2-Turbo"))

    def test_weightless_components_are_not_required_to_have_weights(self):
        """scheduler/ and tokenizer/ legitimately hold only JSON."""
        import tempfile
        g = _gen()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "m"
            self._tree(root, shards_present=True)
            self.assertFalse(any((root / "scheduler").glob("*.safetensors")))
            with patch.object(g, "_get_model_path", return_value=root):
                self.assertTrue(g._is_model_downloaded("x/y"))

    def test_component_with_config_but_no_weights_is_incomplete(self):
        """text_encoder/ arrived as config.json only in the real failure."""
        import tempfile, json
        g = _gen()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "m"
            self._tree(root, shards_present=True)
            (root / "text_encoder").mkdir()
            (root / "text_encoder" / "config.json").write_text("{}")
            idx = json.loads((root / "model_index.json").read_text())
            idx["text_encoder"] = ["transformers", "Qwen3VLModel"]
            (root / "model_index.json").write_text(json.dumps(idx))
            with patch.object(g, "_get_model_path", return_value=root):
                self.assertFalse(g._is_model_downloaded("x/y"))

    def test_absent_optional_components_do_not_count_as_missing(self):
        """save_pretrained writes an absent safety_checker / image_encoder as [null, null]."""
        import tempfile, json
        g = _gen()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "m"
            self._tree(root, shards_present=True)
            idx = json.loads((root / "model_index.json").read_text())
            idx["feature_extractor"] = [None, None]
            idx["image_encoder"] = [None, None]
            idx["safety_checker"] = [None, None]
            (root / "model_index.json").write_text(json.dumps(idx))
            with patch.object(g, "_get_model_path", return_value=root):
                self.assertTrue(g._is_model_downloaded("x/y"))


class TestRepoAccessProbe(unittest.TestCase):

    def _probe(self, status_code, token):
        g = _gen()
        g._repo_access_cache = {}

        class R:
            pass
        R.status_code = status_code

        with patch.object(OfflineImageGenerator, "_hf_token", staticmethod(lambda: token)), \
             patch("requests.head", return_value=R()) as head:
            verdict = g._probe_repo_access("krea/Krea-2-Turbo")
        self.last_call = head.call_args
        return verdict

    def test_200_is_ok(self):
        self.assertEqual(self._probe(200, "hf_x"), "ok")

    def test_403_with_a_token_configured_reads_gated(self):
        self.assertEqual(self._probe(403, "hf_x"), "gated")

    def test_the_probe_never_sends_the_token(self):
        self._probe(200, "hf_x")
        headers = self.last_call.kwargs.get("headers") or {}
        self.assertNotIn("Authorization", headers)

    def test_401_without_token_means_token_needed(self):
        self.assertEqual(self._probe(401, None), "needs_token")

    def test_404_is_unreachable(self):
        self.assertEqual(self._probe(404, "hf_x"), "unreachable")

    def test_network_error_is_unreachable_not_a_crash(self):
        g = _gen()
        g._repo_access_cache = {}
        with patch("requests.head", side_effect=OSError("no route to host")):
            self.assertEqual(g._probe_repo_access("krea/Krea-2-Turbo"), "unreachable")

    def test_verdict_is_cached(self):
        g = _gen()
        g._repo_access_cache = {}

        class R:
            status_code = 200

        with patch.object(OfflineImageGenerator, "_hf_token", staticmethod(lambda: "hf_x")), \
             patch("requests.head", return_value=R()) as head:
            g._probe_repo_access("krea/Krea-2-Turbo")
            g._probe_repo_access("krea/Krea-2-Turbo")

        self.assertEqual(head.call_count, 1, "the menu asks per model; do not refetch")


class TestMenuFiltering(unittest.TestCase):

    def test_a_gated_model_without_a_token_is_not_selectable(self):
        g = _gen()
        with patch.object(g, "_is_model_downloaded", side_effect=lambda mid: "Z-Image" in mid), \
             patch.object(g, "_probe_repo_access", return_value="needs_token"):
            models = g.get_available_models(probe_remote=True)

        self.assertEqual(models["zimage-turbo"]["availability"], "ready")
        self.assertTrue(models["zimage-turbo"]["selectable"])
        self.assertEqual(models["krea2-turbo"]["availability"], "needs_token")
        self.assertFalse(models["krea2-turbo"]["selectable"])

    def test_a_gated_model_with_a_token_stays_selectable_and_says_so(self):
        """The download uses the token; only then does Hugging Face say whether the
        terms were accepted, so the probe must not hide the model."""
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=False), \
             patch.object(g, "_probe_repo_access", return_value="gated"):
            models = g.get_available_models(probe_remote=True)

        self.assertEqual(models["krea2-turbo"]["availability"], "downloadable")
        self.assertTrue(models["krea2-turbo"]["selectable"])
        self.assertTrue(models["krea2-turbo"]["gated"])

    def test_undownloaded_but_fetchable_stays_selectable(self):
        """A model that simply needs downloading must remain pickable, or the user
        can never trigger the first download."""
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=False), \
             patch.object(g, "_probe_repo_access", return_value="ok"):
            models = g.get_available_models(probe_remote=True)

        self.assertEqual(models["sd-xl"]["availability"], "downloadable")
        self.assertTrue(models["sd-xl"]["selectable"])

    def test_listing_models_contacts_no_one_by_default(self):
        """Opening the image page lists models; only Check access asks Hugging Face."""
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=False), \
             patch.object(g, "_probe_repo_access") as probe:
            models = g.get_available_models()
        probe.assert_not_called()
        self.assertEqual(models["krea2-turbo"]["availability"], "downloadable")

    def test_probe_remote_false_skips_the_network(self):
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=False), \
             patch.object(g, "_probe_repo_access") as probe:
            g.get_available_models(probe_remote=False)
        probe.assert_not_called()

    def test_downloaded_models_are_never_probed(self):
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=True), \
             patch.object(g, "_probe_repo_access") as probe:
            g.get_available_models(probe_remote=True)
        probe.assert_not_called()

    def test_undownloaded_user_flux_is_downloadable_not_unreachable(self):
        """User Comfy stills (FLUX) must stay in Manage Image Models for Install."""
        g = _gen()
        mid = "user-flux-fp8"
        g.available_models[mid] = "user:user-flux-fp8"
        g.user_entries[mid] = {"engine": "comfy", "role": "generation", "family": "flux"}
        g.model_meta[mid] = {
            "label": "Flux FP8", "user": True, "engine": "comfy",
            "family": "flux", "kind": "comfy_files", "order": 50,
        }
        with patch.object(g, "_is_model_downloaded", return_value=False), \
             patch.object(g, "_probe_repo_access", return_value="ok"):
            models = g.get_available_models()
        self.assertEqual(models[mid]["availability"], "downloadable")
        self.assertTrue(models[mid]["selectable"])
        self.assertEqual(models["flux-dev"]["availability"], "downloadable")
        self.assertTrue(models["flux-dev"]["selectable"])


class TestLoadFailureMessages(unittest.TestCase):

    def test_gated_repo_names_the_licence_step(self):
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=False), \
             patch.object(g, "_probe_repo_access", return_value="needs_licence"):
            msg = g._load_failure_reason("krea2-turbo", "krea/Krea-2-Turbo")

        self.assertIn("gated", msg.lower())
        self.assertIn("huggingface.co/krea/Krea-2-Turbo", msg)

    def test_gated_with_a_token_points_at_the_terms(self):
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=False), \
             patch.object(g, "_probe_repo_access", return_value="gated"):
            msg = g._load_failure_reason("krea2-turbo", "krea/Krea-2-Turbo")

        self.assertIn("gated", msg.lower())
        self.assertIn("HF_TOKEN", msg)
        self.assertIn("huggingface.co/krea/Krea-2-Turbo", msg)

    def test_missing_token_names_the_env_var(self):
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=False), \
             patch.object(g, "_probe_repo_access", return_value="needs_token"):
            msg = g._load_failure_reason("krea2-raw", "krea/Krea-2-Raw")

        self.assertIn("HF_TOKEN", msg)

    def test_downloaded_but_broken_points_at_the_log(self):
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=True):
            msg = g._load_failure_reason("sd-xl", "stabilityai/stable-diffusion-xl-base-1.0")

        self.assertIn("log", msg.lower())

    def test_no_message_ever_suggests_a_substitute_model(self):
        g = _gen()
        with patch.object(g, "_is_model_downloaded", return_value=False), \
             patch.object(g, "_probe_repo_access", return_value="needs_licence"):
            msg = g._load_failure_reason("krea2-turbo", "krea/Krea-2-Turbo")

        self.assertNotIn("falling back", msg.lower())
        self.assertNotIn("sd-1.5", msg.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
