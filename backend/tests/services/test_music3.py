"""MiniMax Music 3 through ComfyUI: registry contract, the template graph,
the polled job, and the audio route's model switch."""
import pytest

from backend.services import video_model_registry as vmr
from backend.services import comfyui_music_generator as m3

MODEL = "minimax-music3-int8"


def test_registry_entry_is_an_audio_model_with_companions():
    entry = vmr.VIDEO_MODEL_REGISTRY[MODEL]
    assert entry["type"] == "audio" and entry["requires"] == ["minimax-music3-text-encoder", "minimax-music3-dav"]
    assert vmr.music_comfyui_map()[MODEL] == {
        "unet": "minimax_music3_dit_int8_convrot.safetensors",
        "clip": "minimax_music3_text_encoder_pruned_int8_convrot.safetensors",
        "vae": "minimax_music3_dav.safetensors",
    }
    caps = vmr.model_capabilities(MODEL)
    assert caps["min_steps"] == 30 and caps["max_clip_s"] == 300.0 and caps["license"]["attribution"] == "MiniMax-Music3"
    assert caps["modes"] == [] and vmr.supports_first_frame_i2v(MODEL) is False
    assert vmr.verify_registry() == []


def test_preflight_audio_branch(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: False)
    ok, err = vmr.preflight_video_model(MODEL)
    assert ok is False and "not installed" in err
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: False)
    ok, err = vmr.preflight_video_model(MODEL)
    assert ok is False and "0.33" in err
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: True)
    assert vmr.preflight_video_model(MODEL) == (True, "")


def test_workflow_is_the_text_to_music_template():
    wf = m3.build_music_workflow(caption="warm indie folk, female vocal", lyrics="[Verse]\nhello", seconds=90, seed=7)
    assert wf["2"]["inputs"] == {"clip": "minimax_music3_text_encoder_pruned_int8_convrot.safetensors", "type": "minimax", "device": "default"}
    enc = wf["4"]["inputs"]
    assert enc["caption"].startswith("warm indie") and enc["lyrics"].startswith("[Verse]")
    assert enc["max_duration"] == 90.0 and enc["cfg_scale"] == 1.7 and enc["top_k"] == 50 and enc["seed"] == 7
    assert wf["5"]["inputs"]["seconds"] == ["4", 1]
    assert wf["6"]["inputs"]["steps"] == 30 and wf["6"]["inputs"]["cfg"] == 1.7 and wf["6"]["inputs"]["sampler_name"] == "euler"
    assert wf["7"]["class_type"] == "VAEDecodeAudioTiled" and wf["8"]["class_type"] == "SaveAudio"
    assert m3.build_music_workflow(caption="x", seconds=9999)["4"]["inputs"]["max_duration"] == 300.0


def test_generate_runs_the_graph_and_downloads_the_song(monkeypatch, tmp_path):
    class _VG:
        cache_dir = tmp_path
        def _queue_prompt(self, wf, client_id=None):
            self.wf = wf
            return "p1"
        def _wait_for_completion(self, pid, timeout, hard_ceiling_s):
            self.timeout = timeout
            return {"8": {"audio": [{"filename": "song.flac", "type": "output", "subfolder": "audio"}]}}
        def _download_file(self, filename, dest, file_type="output", subfolder=""):
            p = dest / filename
            p.write_bytes(b"x")
            return [str(p)]
    monkeypatch.setattr(vmr, "preflight_video_model", lambda m: (True, ""))
    import contextlib
    monkeypatch.setattr("backend.services.gpu_resource_policy.gpu_session", lambda *a, **k: contextlib.nullcontext(True))
    vg = _VG()
    out = m3.ComfyUIMusicGenerator(vg).generate(caption="c", lyrics="l", seconds=120, steps=10)
    assert out["success"] and out["path"].endswith("song.flac")
    assert out["steps"] == 30            # the floor from the registry, not the 10 asked for
    assert vg.timeout == 1440            # budgeted on the song's length
    assert out["attribution"] == "MiniMax-Music3"


def test_job_runner_records_the_result(monkeypatch):
    class _Gen:
        def __init__(self):
            pass
        def generate(self, **kw):
            return {"success": True, "path": "/x/s.flac", "seconds": kw["seconds"], "model": MODEL}
    monkeypatch.setattr(m3, "ComfyUIMusicGenerator", _Gen)
    job_id = m3.start_job(caption="c", lyrics="", seconds=30, seed=None, steps=None, model_id=MODEL)
    import time
    for _ in range(50):
        job = m3.job_status(job_id)
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "done" and job["path"] == "/x/s.flac"
    assert m3.job_status("nope") is None


def test_audio_route_switches_on_model(monkeypatch):
    from flask import Flask
    from backend.api.audio_foundry_api import audio_foundry_bp
    app = Flask(__name__)
    app.register_blueprint(audio_foundry_bp)
    monkeypatch.setattr(vmr, "preflight_video_model", lambda m: (True, ""))
    monkeypatch.setattr(m3, "start_job", lambda **kw: "job42")
    client = app.test_client()
    resp = client.post("/api/audio-foundry/generate/music", json={
        "model": MODEL, "style_prompt": "warm folk", "lyrics": "[Verse] la", "duration_s": 45,
    })
    assert resp.status_code == 202
    body = resp.get_json()
    assert body["job_id"] == "job42" and body["attribution"] == "MiniMax-Music3"
    monkeypatch.setattr(vmr, "preflight_video_model", lambda m: (False, "not installed"))
    resp = client.post("/api/audio-foundry/generate/music", json={"model": MODEL, "style_prompt": "x"})
    assert resp.status_code == 400 and "not installed" in resp.get_json()["error"]
