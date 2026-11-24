#!/usr/bin/env python3
"""Whisper weights arrive only through a visible Install.

Transcribing must never fetch weights: faster-whisper loads with
local_files_only=True and the ggml check only reports. A missing model comes
back as "Install the speech model to use voice", and the voice model Install
(/models/download, /install-whisper-model) is the one path that downloads.
"""

import io
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask  # noqa: E402

import backend.api.voice_api as voice_api  # noqa: E402
from backend.utils import faster_whisper_utils as fw  # noqa: E402

MESSAGE = "Install the speech model to use voice"


def _client():
    app = Flask(__name__)
    app.register_blueprint(voice_api.voice_bp)
    app.config["TESTING"] = True
    return app.test_client()


class _OfflineHub:
    """Stands in for faster_whisper's download_model and records every call.

    Any call without local_files_only=True is a download and fails the test.
    """

    def __init__(self, cached_path=None):
        self.cached_path = cached_path
        self.calls = []

    def __call__(self, size_or_id, **kwargs):
        self.calls.append((size_or_id, kwargs))
        if not kwargs.get("local_files_only"):
            raise AssertionError(f"download attempted for {size_or_id}")
        if self.cached_path is None:
            raise FileNotFoundError("not in the local cache")
        return self.cached_path


class TestFasterWhisperLoadsLocalOnly(unittest.TestCase):

    def setUp(self):
        for name, value in (("FASTER_WHISPER_AVAILABLE", True),
                            ("_whisper_model", None),
                            ("_current_model_size", None)):
            patcher = patch.object(fw, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_weights_raise_the_plain_message_without_a_download(self):
        hub = _OfflineHub(cached_path=None)
        model_cls = MagicMock()
        with patch.object(fw, "download_model", hub), \
             patch.object(fw, "WhisperModel", model_cls):
            with self.assertRaises(fw.SpeechModelMissing) as caught:
                fw.get_faster_whisper_model("tiny.en", device="cpu")

        self.assertEqual(str(caught.exception), MESSAGE)
        self.assertEqual(caught.exception.model_size, "tiny.en")
        model_cls.assert_not_called()
        self.assertTrue(hub.calls)
        self.assertTrue(all(kw.get("local_files_only") for _, kw in hub.calls))

    def test_transcribe_reports_missing_weights_the_same_way(self):
        with patch.object(fw, "download_model", _OfflineHub(cached_path=None)), \
             patch.object(fw, "WhisperModel", MagicMock()):
            with self.assertRaises(fw.SpeechModelMissing):
                fw.transcribe_audio_faster(b"", model_size="tiny.en")

    def test_snapshot_without_model_bin_counts_as_missing(self):
        with patch.object(fw, "download_model", _OfflineHub(cached_path="/nonexistent/snapshot")):
            self.assertIsNone(fw.local_model_path("tiny.en"))
            self.assertFalse(fw.is_model_installed("tiny.en"))

    def test_installed_weights_load_with_local_files_only(self):
        snapshot = os.path.dirname(os.path.abspath(__file__))
        model_cls = MagicMock()
        with patch.object(fw, "download_model", _OfflineHub(cached_path=snapshot)), \
             patch.object(fw, "WhisperModel", model_cls), \
             patch("os.path.isfile", return_value=True):
            fw.get_faster_whisper_model("tiny.en", device="cpu")

        model_cls.assert_called_once()
        self.assertIs(model_cls.call_args.kwargs.get("local_files_only"), True)

    def test_int8_fallback_also_loads_local_only(self):
        snapshot = os.path.dirname(os.path.abspath(__file__))
        model_cls = MagicMock(side_effect=[RuntimeError("float16 unsupported"), MagicMock()])
        with patch.object(fw, "download_model", _OfflineHub(cached_path=snapshot)), \
             patch.object(fw, "WhisperModel", model_cls), \
             patch("os.path.isfile", return_value=True):
            fw.get_faster_whisper_model("tiny.en", device="cpu", compute_type="float16")

        self.assertEqual(model_cls.call_count, 2)
        for call in model_cls.call_args_list:
            self.assertIs(call.kwargs.get("local_files_only"), True)

    def test_install_model_is_the_download(self):
        hub = MagicMock(return_value="/cache/snapshot")
        with patch.object(fw, "download_model", hub):
            fw.install_model("tiny.en")
        hub.assert_called_once_with("tiny.en", use_auth_token=False)

    def test_turbo_install_check_stays_offline(self):
        hub = _OfflineHub(cached_path=None)
        with patch.object(fw, "download_model", hub):
            self.assertFalse(fw.is_model_installed("large-v3-turbo"))
        self.assertEqual(hub.calls, [("large-v3-turbo", {"local_files_only": True})])


class TestGgmlCheckOnly(unittest.TestCase):

    def test_missing_ggml_is_reported_not_downloaded(self):
        with patch.object(voice_api, "file_problem", return_value=voice_api.MISSING), \
             patch.object(voice_api, "_download_ggml_model_direct") as download, \
             patch("urllib.request.urlopen") as urlopen:
            ok, path, error = voice_api.ensure_whisper_model_downloaded(
                voice_api.WHISPER_MODELS["tiny.en"]
            )

        self.assertFalse(ok)
        self.assertTrue(path.endswith("ggml-tiny.en.bin"))
        self.assertIn("not installed", error)
        download.assert_not_called()
        urlopen.assert_not_called()


@unittest.skipUnless(fw.FASTER_WHISPER_AVAILABLE, "faster-whisper is not installed")
class TestSpeechToTextReportsInstall(unittest.TestCase):

    def test_missing_model_returns_the_plain_message_and_frees_the_slot(self):
        import numpy as np

        client = _client()
        with patch.object(voice_api, "USE_WHISPER_SERVER", False), \
             patch.object(voice_api, "check_rate_limit", return_value=(True, "ok")), \
             patch.object(voice_api, "release_rate_limit") as release, \
             patch.object(voice_api.process_monitor, "get_system_status",
                          return_value={"system_overloaded": False}), \
             patch("backend.utils.faster_whisper_utils.decode_audio", return_value=np.zeros(16000)), \
             patch.object(fw, "transcribe_audio_faster",
                          side_effect=fw.SpeechModelMissing("tiny.en")):
            response = client.post(
                "/api/voice/speech-to-text",
                data={"audio": (io.BytesIO(b"\x00" * 64), "audio.webm")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertEqual(body["error"], MESSAGE)
        self.assertEqual(body["code"], "SPEECH_MODEL_MISSING")
        self.assertEqual(body["model_id"], "tiny.en")
        release.assert_called_once()


class TestStatusOffersTheInstall(unittest.TestCase):

    def _status(self, missing):
        with patch.object(voice_api, "_whisper_missing_parts", return_value=missing), \
             patch.object(voice_api, "escalation_method", return_value="none"):
            return _client().get("/api/voice/status").get_json()

    def test_missing_weights_report_not_installed(self):
        body = self._status(["faster-whisper"])
        self.assertIs(body["speech_model_installed"], False)
        self.assertEqual(body["speech_model_id"], voice_api.DEFAULT_WHISPER_MODEL)

    def test_complete_weights_report_installed(self):
        self.assertIs(self._status([])["speech_model_installed"], True)

    def test_status_names_the_model_chosen_in_settings(self):
        with patch("backend.utils.settings_utils.get_setting", return_value="small"), \
             patch.object(fw, "is_model_installed", return_value=True):
            body = self._status([])
        self.assertEqual(body["speech_model_id"], "small")
        self.assertIs(body["speech_model_installed"], True)


class TestMissingParts(unittest.TestCase):

    def test_both_formats_count_where_whisper_cpp_is_built(self):
        config = voice_api.WHISPER_MODELS["tiny.en"]
        with patch("os.path.exists", side_effect=lambda p: p.endswith("whisper-cli")), \
             patch.object(fw, "FASTER_WHISPER_AVAILABLE", True), \
             patch.object(fw, "is_model_installed", return_value=False):
            missing = voice_api._whisper_missing_parts("/backend", "tiny.en", config)
        self.assertEqual(missing, ["ggml", "faster-whisper"])

    def test_ggml_not_needed_without_whisper_cpp(self):
        config = voice_api.WHISPER_MODELS["tiny.en"]
        with patch("os.path.exists", return_value=False), \
             patch.object(fw, "FASTER_WHISPER_AVAILABLE", True), \
             patch.object(fw, "is_model_installed", return_value=False):
            missing = voice_api._whisper_missing_parts("/backend", "tiny.en", config)
        self.assertEqual(missing, ["faster-whisper"])

    def test_faster_whisper_weights_not_needed_without_the_package(self):
        config = voice_api.WHISPER_MODELS["tiny.en"]
        with patch("os.path.exists", return_value=True), \
             patch.object(voice_api, "file_complete", return_value=True), \
             patch.object(fw, "FASTER_WHISPER_AVAILABLE", False):
            missing = voice_api._whisper_missing_parts("/backend", "tiny.en", config)
        self.assertEqual(missing, [])


class TestInstallDownloads(unittest.TestCase):

    def test_install_fetches_only_the_missing_parts(self):
        config = voice_api.WHISPER_MODELS["tiny.en"]
        with patch.object(voice_api, "_whisper_missing_parts", return_value=["faster-whisper"]), \
             patch.object(voice_api, "_download_ggml_model_direct") as ggml, \
             patch.object(fw, "install_model") as install, \
             patch.object(fw, "is_model_installed", return_value=True):
            voice_api._do_whisper_download("/backend", "tiny.en", config)

        ggml.assert_not_called()
        install.assert_called_once_with("tiny.en")

    def test_install_fetches_ggml_and_weights_on_a_fresh_machine(self):
        config = voice_api.WHISPER_MODELS["tiny.en"]
        with patch.object(voice_api, "_whisper_missing_parts",
                          return_value=["ggml", "faster-whisper"]), \
             patch.object(voice_api, "_download_ggml_model_direct") as ggml, \
             patch.object(fw, "install_model") as install, \
             patch.object(fw, "is_model_installed", return_value=True):
            voice_api._do_whisper_download("/backend", "tiny.en", config)

        ggml.assert_called_once()
        self.assertEqual(ggml.call_args.args[0], "tiny.en")
        install.assert_called_once_with("tiny.en")

    def test_turbo_installs_under_one_id_for_both_engines(self):
        # whisper.cpp publishes ggml-large-v3-turbo.bin; faster-whisper maps
        # "large-v3-turbo" to its CTranslate2 repo.
        config = voice_api.WHISPER_MODELS["large-v3-turbo"]
        with patch.object(voice_api, "_whisper_missing_parts",
                          return_value=["ggml", "faster-whisper"]), \
             patch.object(voice_api, "_download_ggml_model_direct") as ggml, \
             patch.object(fw, "install_model") as install, \
             patch.object(fw, "is_model_installed", return_value=True):
            voice_api._do_whisper_download("/backend", "large-v3-turbo", config)

        self.assertEqual(ggml.call_args.args[0], "large-v3-turbo")
        self.assertTrue(ggml.call_args.args[1].endswith("ggml-large-v3-turbo.bin"))
        install.assert_called_once_with("large-v3-turbo")

    def test_incomplete_weights_after_download_fail_the_install(self):
        config = voice_api.WHISPER_MODELS["tiny.en"]
        with patch.object(voice_api, "_whisper_missing_parts", return_value=["faster-whisper"]), \
             patch.object(fw, "install_model"), \
             patch.object(fw, "is_model_installed", return_value=False):
            with self.assertRaises(RuntimeError):
                voice_api._do_whisper_download("/backend", "tiny.en", config)

    def test_install_click_runs_the_download(self):
        client = _client()
        with patch.object(voice_api, "_whisper_missing_parts", return_value=["faster-whisper"]), \
             patch.object(fw, "weights_cache_dir", return_value=None), \
             patch.object(voice_api, "_do_whisper_download") as download:
            response = client.post(
                "/api/voice/models/download",
                json={"model_type": "whisper", "model_id": "tiny.en"},
            )
            self.assertEqual(response.status_code, 200)
            deadline = time.time() + 5
            while time.time() < deadline:
                with voice_api._voice_download_lock:
                    if not voice_api._voice_download_status["is_downloading"]:
                        break
                time.sleep(0.05)

        download.assert_called_once()
        self.assertEqual(download.call_args.args[1], "tiny.en")
        with voice_api._voice_download_lock:
            self.assertEqual(voice_api._voice_download_status["status"], "completed")

    def test_unknown_model_type_is_refused(self):
        response = _client().post(
            "/api/voice/models/download",
            json={"model_type": "bark", "model_id": "tiny.en"},
        )
        self.assertEqual(response.status_code, 400)

    def test_install_whisper_model_route_downloads(self):
        with patch.object(voice_api, "_do_whisper_download") as download:
            response = _client().post(
                "/api/voice/install-whisper-model", json={"model_id": "tiny.en"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        download.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
