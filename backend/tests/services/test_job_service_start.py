"""Starting a GPU service for the job that needs it (GUAARDVARK_JOB_SERVICE_START).

The plugin start (plugin_bridge._try_start_plugin, which calls the plugin
manager) and the services' HTTP endpoints are the seams replaced here. The
stage lookup, the gating, the wait and each caller's handling are real.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

try:
    from flask import Flask
    from backend.services import plugin_bridge
    from backend.services.plugin_bridge import JOB_SERVICE_START_ENV, start_for_job
    from backend.services import comfyui_image_generator as cig
    from backend.services import video_model_registry as vmr
    from backend.api import audio_foundry_api as afa
    from backend.api import upscaling_api as upa
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


REPO = Path(__file__).resolve().parents[3]


class Services:
    """Which plugins answer, and which starts were asked for."""

    def __init__(self):
        self.up = set()
        self.started = []
        self.refuse = {}

    def start(self, plugin_id, **kw):
        self.started.append(plugin_id)
        if plugin_id in self.refuse:
            return False, self.refuse[plugin_id]
        self.up.add(plugin_id)
        return True, "started"


@pytest.fixture
def services(monkeypatch):
    svc = Services()
    monkeypatch.setattr(plugin_bridge, "_try_start_plugin", svc.start)
    # The stage prep inside ensure_plugins_for_stage books nothing for these
    # contexts, but it builds the orchestrator; keep it out of the test.
    import backend.services.gpu_memory_orchestrator as gmo
    monkeypatch.setattr(gmo, "get_orchestrator", lambda: SimpleNamespace(prepare_for_stage=lambda c, s: {}))
    monkeypatch.setattr(plugin_bridge, "_stage_wait_s", lambda c, s: 0.0)
    monkeypatch.delenv(JOB_SERVICE_START_ENV, raising=False)
    monkeypatch.delenv("GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR", raising=False)
    monkeypatch.delenv("GUAARDVARK_MCP_PROCESS", raising=False)
    return svc


def _on(monkeypatch):
    monkeypatch.setenv(JOB_SERVICE_START_ENV, "1")


# ── the stage map ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("context,plugin", [
    ("image", "comfyui"), ("audio", "audio_foundry"), ("upscale", "upscaling"),
])
def test_each_job_context_names_a_real_plugin(context, plugin):
    assert plugin_bridge.plugins_for_stage(context, "generating") == [plugin]
    manifest = json.loads((REPO / "plugins" / plugin / "plugin.json").read_text())
    assert manifest["id"] == plugin


# ── start_for_job ──────────────────────────────────────────────────────────

def test_off_by_default_starts_nothing(services):
    ok, why = start_for_job("image", "generating", is_up=lambda: "comfyui" in services.up)
    assert ok is False and JOB_SERVICE_START_ENV in why
    assert services.started == []


def test_a_running_service_is_left_alone(services, monkeypatch):
    _on(monkeypatch)
    services.up.add("comfyui")
    assert start_for_job("image", "generating", is_up=lambda: "comfyui" in services.up) == (True, "")
    assert services.started == []


def test_starts_the_stage_plugin_and_waits_for_it(services, monkeypatch):
    _on(monkeypatch)
    assert start_for_job("audio", "generating", is_up=lambda: "audio_foundry" in services.up) == (True, "")
    assert services.started == ["audio_foundry"]


def test_the_orchestrator_switch_wins(services, monkeypatch):
    _on(monkeypatch)
    monkeypatch.setenv("GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR", "0")
    ok, why = start_for_job("image", "generating", is_up=lambda: False)
    assert ok is False and "AUTO_ORCHESTRATOR" in why
    assert services.started == []


def test_never_starts_from_the_mcp_process(services, monkeypatch):
    _on(monkeypatch)
    monkeypatch.setenv("GUAARDVARK_MCP_PROCESS", "1")
    ok, why = start_for_job("image", "generating", is_up=lambda: False)
    assert ok is False and "MCP" in why
    assert services.started == []


def test_a_user_disabled_plugin_is_reported_not_started(services, monkeypatch):
    _on(monkeypatch)
    services.refuse["upscaling"] = "plugin 'upscaling' is user-disabled"
    ok, why = start_for_job("upscale", "generating", is_up=lambda: False)
    assert ok is False and "user-disabled" in why


def test_a_service_that_never_answers_times_out(services, monkeypatch):
    _on(monkeypatch)
    ok, why = start_for_job("image", "generating", is_up=lambda: False, wait_s=0)
    assert ok is False and "did not answer" in why
    assert services.started == ["comfyui"]


def test_an_undeclared_stage_starts_nothing(services, monkeypatch):
    _on(monkeypatch)
    ok, why = start_for_job("image", "no-such-stage", is_up=lambda: False)
    assert ok is False and "no plugin is declared" in why
    assert services.started == []


def test_wait_budget_is_the_plugin_manifest_timeout(monkeypatch):
    manifest = json.loads((REPO / "plugins" / "audio_foundry" / "plugin.json").read_text())
    declared = float(manifest["config"]["timeout"])
    meta = SimpleNamespace(config=SimpleNamespace(timeout=declared))
    pm = SimpleNamespace(registry=SimpleNamespace(get_plugin=lambda pid: meta))
    monkeypatch.setattr(plugin_bridge, "_plugin_manager", lambda: pm)
    assert plugin_bridge._stage_wait_s("audio", "generating") == max(30.0, declared)


# ── ComfyUI image generator ────────────────────────────────────────────────

@pytest.fixture
def comfy(services, monkeypatch):
    """ComfyUI's root URL answers only while the comfyui plugin is up."""
    def get(url, timeout=None, **kw):
        if "comfyui" not in services.up:
            raise requests.exceptions.ConnectionError("refused")
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(cig, "requests", SimpleNamespace(get=get, exceptions=requests.exceptions))
    return cig.ComfyUIImageGenerator(comfy_url="http://127.0.0.1:8188")


def test_image_down_and_start_off_keeps_the_old_error(comfy, services):
    with pytest.raises(RuntimeError) as err:
        comfy._require_up("ComfyUI not reachable at http://127.0.0.1:8188 — cannot edit image")
    assert str(err.value) == "ComfyUI not reachable at http://127.0.0.1:8188 — cannot edit image"
    assert services.started == []


def test_image_down_and_start_on_starts_comfyui(comfy, services, monkeypatch):
    _on(monkeypatch)
    comfy._require_up("down")
    assert services.started == ["comfyui"]


def test_image_start_refused_says_why(comfy, services, monkeypatch):
    _on(monkeypatch)
    services.refuse["comfyui"] = "plugin 'comfyui' is user-disabled"
    with pytest.raises(RuntimeError, match="user-disabled"):
        comfy._require_up("ComfyUI not reachable")


def test_an_edit_model_that_is_not_installed_never_starts_comfyui(comfy, services, monkeypatch, tmp_path):
    _on(monkeypatch)
    monkeypatch.setattr(cig.ComfyUIImageGenerator, "qwen_edit_installed", lambda self: False)
    with pytest.raises(RuntimeError):
        comfy.edit_image_qwen(image_paths=[str(tmp_path / "a.png")], instruction="x",
                              output_path=str(tmp_path / "o.png"))
    assert services.started == []


# ── Audio Foundry proxy ────────────────────────────────────────────────────

@pytest.fixture
def audio(services, monkeypatch):
    posted = []

    def get(url, timeout=None, **kw):
        if "audio_foundry" not in services.up:
            raise requests.ConnectionError("refused")
        return SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})

    def post(url, json=None, timeout=None, **kw):
        if "audio_foundry" not in services.up:
            raise requests.ConnectionError("refused")
        posted.append(url)
        return SimpleNamespace(status_code=202, json=lambda: {"job_id": "j1"})

    monkeypatch.setattr(afa, "requests", SimpleNamespace(
        get=get, post=post, delete=get,
        ConnectionError=requests.ConnectionError, Timeout=requests.Timeout,
        RequestException=requests.RequestException,
    ))
    app = Flask(__name__)
    app.register_blueprint(afa.audio_foundry_bp)
    return app.test_client(), posted


def test_audio_down_and_start_off_is_the_old_503(audio, services):
    client, posted = audio
    r = client.post("/api/audio-foundry/generate/fx", json={"prompt": "rain"})
    assert r.status_code == 503 and r.get_json()["error"] == "Audio Foundry service not running"
    assert services.started == [] and posted == []


def test_audio_generate_starts_audio_foundry(audio, services, monkeypatch):
    _on(monkeypatch)
    client, posted = audio
    r = client.post("/api/audio-foundry/generate/music", json={"style_prompt": "lofi"})
    assert r.status_code == 202
    assert services.started == ["audio_foundry"]
    assert posted == ["http://127.0.0.1:8206/generate/music"]


def test_audio_health_poll_never_starts_it(audio, services, monkeypatch):
    _on(monkeypatch)
    client, _ = audio
    assert client.get("/api/audio-foundry/health").status_code == 503
    assert client.get("/api/audio-foundry/status").status_code == 503
    assert services.started == []


def test_audio_start_refused_is_a_503_with_the_reason(audio, services, monkeypatch):
    _on(monkeypatch)
    services.refuse["audio_foundry"] = "plugin 'audio_foundry' is user-disabled"
    client, posted = audio
    r = client.post("/api/audio-foundry/generate/fx", json={"prompt": "rain"})
    assert r.status_code == 503 and "user-disabled" in r.get_json()["error"]
    assert posted == []


def test_music3_starts_comfyui_through_the_video_path(audio, services, monkeypatch):
    _on(monkeypatch)
    client, _ = audio
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: "comfyui" in services.up)
    monkeypatch.setattr(vmr, "COMFYUI_AUTOSTART_WAIT_S", 0)
    import backend.services.comfyui_music_generator as m3
    monkeypatch.setattr(m3, "start_job", lambda **kw: "m3-job")
    model = next(m for m, e in vmr.VIDEO_MODEL_REGISTRY.items() if m.startswith("minimax-music3"))
    r = client.post("/api/audio-foundry/generate/music", json={"model": model, "style_prompt": "x"})
    assert r.status_code == 202, r.get_json()
    assert services.started == ["comfyui"]


# ── Upscaling proxy ────────────────────────────────────────────────────────

@pytest.fixture
def upscaler(services, monkeypatch):
    posted = []

    def get(url, timeout=None, **kw):
        if "upscaling" not in services.up:
            raise requests.ConnectionError("refused")
        return SimpleNamespace(status_code=200, json=lambda: {"status": "ok", "auth_token": "t"})

    def post(url, json=None, headers=None, timeout=None, **kw):
        if "upscaling" not in services.up:
            raise requests.ConnectionError("refused")
        posted.append(url)
        return SimpleNamespace(status_code=202, json=lambda: {"job_id": "u1"})

    monkeypatch.setattr(upa, "requests", SimpleNamespace(
        get=get, post=post,
        ConnectionError=requests.ConnectionError, Timeout=requests.Timeout,
        RequestException=requests.RequestException,
    ))
    monkeypatch.setattr(upa, "_cached_token", None, raising=False)
    app = Flask(__name__)
    app.register_blueprint(upa.upscaling_bp)
    return app.test_client(), posted


def test_upscale_down_and_start_off_is_the_old_503(upscaler, services):
    client, posted = upscaler
    r = client.post("/api/upscaling/upscale/video", json={"input_path": "/tmp/in.mp4"})
    assert r.status_code == 503 and services.started == [] and posted == []


def test_upscale_job_starts_the_upscaler(upscaler, services, monkeypatch):
    _on(monkeypatch)
    client, posted = upscaler
    r = client.post("/api/upscaling/upscale/video", json={"input_path": "/tmp/in.mp4"})
    assert r.status_code == 200 and r.get_json()["data"] == {"job_id": "u1"}
    assert services.started == ["upscaling"]
    assert posted == ["http://localhost:8202/upscale/video"]


def test_upscale_health_never_starts_it(upscaler, services, monkeypatch):
    _on(monkeypatch)
    client, _ = upscaler
    assert client.get("/api/upscaling/health").status_code == 503
    assert services.started == []


# ── Film Crew editor ───────────────────────────────────────────────────────

@pytest.fixture
def rendering_production(services, monkeypatch):
    from backend.models import db, Production
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: "comfyui" in services.up)
    monkeypatch.setattr(vmr, "COMFYUI_AUTOSTART_WAIT_S", 0)
    with app.app_context():
        db.create_all()
        prod = Production(name="P", script_text="x", status="rendering", current_stage="rendering",
                          settings_json={"video_model": "wan22-5b"})
        db.session.add(prod)
        db.session.commit()
        yield db, prod
        db.session.remove()
        db.drop_all()


def _run_editor(prod):
    from backend.tasks.production_swarm_tasks import run_editor
    # No approved shots: once the model resolves, the editor stops there.
    run_editor(prod.id, i2v=object(), audio_foundry=object(), ffmpeg=object())


def test_editor_with_comfyui_down_and_start_off_fails_on_comfyui(rendering_production, services):
    db, prod = rendering_production
    _run_editor(prod)
    db.session.refresh(prod)
    assert prod.status == "failed_rendering"
    assert "ComfyUI" in str(prod.error_blob["error"])
    assert services.started == []


def test_editor_starts_comfyui_for_its_model(rendering_production, services, monkeypatch):
    _on(monkeypatch)
    db, prod = rendering_production
    _run_editor(prod)
    db.session.refresh(prod)
    assert services.started == ["comfyui"]
    assert prod.error_blob["error"] == "No approved shots"
