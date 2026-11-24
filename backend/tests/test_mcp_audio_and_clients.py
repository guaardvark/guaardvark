"""generate_music / generate_speech for MCP clients, songs in get_generation_status,
the Codex / Antigravity / opencode installers, a swarm asked for in plain words,
and photo edits that take an image-batch URL. Backend calls are faked: running
these tests starts no render and touches no client config."""

import json
from pathlib import Path

import pytest
from flask import Flask


def _fake_backend(monkeypatch, answers):
    from backend.utils import backend_http

    sent = []

    def fake_request_json(method, path, payload=None, **kwargs):
        sent.append((method, path, payload))
        answer = answers[path]
        if isinstance(answer, Exception):
            raise answer
        return backend_http.BackendResponse(status=200, body=answer,
                                            data=answer.get("data", answer) if isinstance(answer, dict) else answer)

    monkeypatch.setattr(backend_http, "request_json", fake_request_json)
    return sent


# ---- generate_music ----------------------------------------------------------------------
def test_music_queues_an_async_song(monkeypatch):
    from backend.tools.audio_tools import GenerateMusicTool

    sent = _fake_backend(monkeypatch, {"/api/audio-foundry/generate/music": {
        "mode": "async", "job_id": "a" * 32, "status": "queued", "estimate_s": 45.0}})
    res = GenerateMusicTool().execute(style="synthwave, analog bass, 110 bpm", lyrics="[verse]\nhi",
                                      seconds=30)
    assert res.success and res.output["job_id"] == "a" * 32
    payload = sent[0][2]
    assert payload == {"style_prompt": "synthwave, analog bass, 110 bpm", "duration_s": 30.0,
                       "instrumental_only": False, "async": True, "lyrics": "[verse]\nhi"}
    assert "get_generation_status" in res.output["next"]


def test_music_instrumental_drops_lyrics_and_checks_length(monkeypatch):
    from backend.tools.audio_tools import GenerateMusicTool

    sent = _fake_backend(monkeypatch, {"/api/audio-foundry/generate/music": {"job_id": "b" * 32}})
    GenerateMusicTool().execute(style="ambient", lyrics="ignored", instrumental=True)
    assert "lyrics" not in sent[0][2] and sent[0][2]["instrumental_only"] is True
    assert GenerateMusicTool().execute(style="ambient", seconds=900).success is False
    assert GenerateMusicTool().execute(style="  ").success is False


def test_music_without_lyrics_is_sent_as_instrumental(monkeypatch):
    from backend.tools.audio_tools import GenerateMusicTool

    sent = _fake_backend(monkeypatch, {"/api/audio-foundry/generate/music": {"job_id": "c" * 32}})
    GenerateMusicTool().execute(style="relaxing music for working")
    GenerateMusicTool().execute(style="ambient", lyrics="   ")
    for _, _, payload in sent:
        assert payload["instrumental_only"] is True and "lyrics" not in payload


def test_music_model_15_is_sent_and_the_default_is_left_out(monkeypatch):
    from backend.tools.audio_tools import GenerateMusicTool

    sent = _fake_backend(monkeypatch, {"/api/audio-foundry/generate/music": {"job_id": "d" * 32}})
    res = GenerateMusicTool().execute(style="ambient", model="ace-step-1.5")
    assert res.success and sent[0][2]["model"] == "ace-step-1.5"
    GenerateMusicTool().execute(style="ambient", model="ace-step")
    assert "model" not in sent[1][2]
    bad = GenerateMusicTool().execute(style="ambient", model="suno")
    assert bad.success is False and "ace-step-1.5" in bad.error
    assert len(sent) == 2


def test_music_says_when_audio_foundry_is_off(monkeypatch):
    from backend.tools.audio_tools import GenerateMusicTool
    from backend.utils import backend_http

    _fake_backend(monkeypatch, {"/api/audio-foundry/generate/music":
                                backend_http.BackendError("plugin_offline", "not running", status=503)})
    res = GenerateMusicTool().execute(style="jazz")
    assert res.success is False and "Audio Foundry is not running" in res.error


# ---- generate_speech ---------------------------------------------------------------------
def test_speech_returns_the_file_and_a_download_link(monkeypatch):
    from backend.tools.audio_tools import GenerateSpeechTool

    sent = _fake_backend(monkeypatch, {"/api/audio-foundry/generate/voice": {
        "path": "/srv/x/data/uploads/Audio/voice_1.wav", "duration_s": 2.43, "document_id": 77,
        "meta": {"backend": "kokoro", "voice": "af_heart"}}})
    res = GenerateSpeechTool().execute(text="Dinner is served.", voice="af_heart", engine="kokoro")
    assert res.success
    assert sent[0][2] == {"text": "Dinner is served.", "backend": "kokoro", "voice_id": "af_heart",
                          "async": True, "queue": True}
    out = res.output
    assert out["file"] == "voice_1.wav" and out["document_id"] == 77
    assert out["url"] == "/api/files/document/77/download"
    assert out["download"].endswith("/api/files/document/77/download")
    assert "/srv/x" not in json.dumps(out)
    assert out["engine"] == "kokoro" and out["voice"] == "af_heart"


def test_speech_refuses_overlong_text_and_offers_no_cloning():
    from backend.tools.audio_tools import GenerateSpeechTool, MAX_SPEECH_CHARS

    tool = GenerateSpeechTool()
    assert tool.execute(text="x" * (MAX_SPEECH_CHARS + 1)).success is False
    assert tool.execute(text="hi", engine="piper").success is False
    assert "reference_clip_path" not in tool.parameters


# ---- registration ------------------------------------------------------------------------
def test_speech_registers_in_every_process_and_music_only_in_the_mcp_process(monkeypatch):
    """Chat makes speech when a message asks to hear words; songs stay with MCP clients and the Studio."""
    from backend.tools import tool_registry_init

    sent = []
    monkeypatch.setattr(tool_registry_init, "register_tool", lambda tool: sent.append(tool.name))
    monkeypatch.setattr(tool_registry_init, "_tool_categories", {})
    monkeypatch.delenv("GUAARDVARK_MCP_PROCESS", raising=False)
    assert tool_registry_init.register_audio_tools() == ["generate_speech"]
    assert sent == ["generate_speech"]
    assert tool_registry_init.get_tool_categories()["generate_speech"] == "audio"

    sent.clear()
    monkeypatch.setenv("GUAARDVARK_MCP_PROCESS", "1")
    assert tool_registry_init.register_audio_tools() == ["generate_music", "generate_speech"]
    assert sent == ["generate_music", "generate_speech"]
    assert tool_registry_init.get_tool_categories()["generate_music"] == "audio"


# ---- get_generation_status ---------------------------------------------------------------
def test_status_reports_a_finished_song(monkeypatch):
    from backend.tools import image_tools

    job = {"id": "c" * 32, "intent": "music", "status": "done", "progress": {"current": 1, "total": 1},
           "result": {"path": "/srv/x/data/uploads/Audio/song.wav", "duration_s": 30.1, "document_id": 9}}
    monkeypatch.setattr(image_tools, "_http_json", lambda method, path, *a, **k: job)
    tool = image_tools.GenerationStatusTool()
    tool.set_context({"transport": "mcp"})
    res = tool.execute(batch_id="c" * 32)
    assert res.success, res.error
    assert "Song job" in res.output and "complete" in res.output
    assert "/api/files/document/9/download" in res.output
    assert "/srv/x" not in res.output


# ---- installer ---------------------------------------------------------------------------
@pytest.mark.parametrize("client,argv0", [("codex", "codex"), ("antigravity", "agy")])
def test_cli_clients_are_configured_through_their_own_mcp_add(client, argv0, monkeypatch, tmp_path):
    from backend.mcp import installer

    # The installer reads the client's config to see what is already there.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    detail = installer._INSTALLERS[client](dry_run=True)
    assert detail.startswith(f"would run: {argv0} mcp add guaardvark -- sh -c")


def test_opencode_entry_is_merged_into_its_json(monkeypatch, tmp_path):
    from backend.mcp import installer

    cfg = tmp_path / "opencode" / "opencode.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"mcp": {"other": {"type": "local", "command": ["x"]}}, "model": "m"}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    installer._install_opencode(dry_run=False)
    data = json.loads(cfg.read_text())
    assert data["model"] == "m" and "other" in data["mcp"]
    entry = data["mcp"]["guaardvark"]
    assert entry["type"] == "local" and entry["enabled"] is True and entry["command"][:2] == ["sh", "-c"]
    assert (cfg.parent / "opencode.json.guaardvark-backup").is_file()


def test_every_installer_client_is_an_argparse_choice():
    from backend.mcp.__main__ import _build_parser
    from backend.mcp.installer import CLIENTS

    args = _build_parser().parse_args(["install", *sum((["--client", c] for c in CLIENTS), [])])
    assert args.clients == list(CLIENTS)


# ---- swarm from a sentence ---------------------------------------------------------------
def test_swarm_prompt_becomes_a_one_task_plan_outside_the_checkout(monkeypatch, tmp_path):
    pytest.importorskip("llama_index")
    from backend import config
    from backend.api import swarm_api

    monkeypatch.setattr(config, "GUAARDVARK_ROOT", str(tmp_path))
    monkeypatch.setattr(swarm_api, "default_repo_root", lambda: tmp_path / "self")
    seen = {}

    def fake_post(path, json_data=None, timeout=swarm_api.SWARM_TIMEOUT):
        seen["body"] = dict(json_data)
        return {"success": True, "swarm_id": "s1"}, 200

    monkeypatch.setattr(swarm_api, "_proxy_post", fake_post)
    (tmp_path / "elsewhere" / ".git").mkdir(parents=True)
    app = Flask(__name__)
    app.register_blueprint(swarm_api.swarm_bp)
    with app.test_client() as client:
        response = client.post("/api/swarm/launch", json={
            "prompt": "fix the failing tests", "repo_path": str(tmp_path / "elsewhere")})
    assert response.status_code == 200, response.get_json()
    rel = seen["body"]["plan_path"]
    assert rel.startswith("data/outputs/swarm_plans/") and "prompt" not in seen["body"]
    plan = (tmp_path / rel).read_text()
    assert "## Task: fix the failing tests" in plan


# ---- photo edits take an image-batch URL -------------------------------------------------
def test_edit_resolves_a_batch_image_url(monkeypatch, tmp_path):
    from backend import config
    from backend.tools.image_tools import EditImageTool

    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "out"))
    img = tmp_path / "Images" / "ImageBatch_1" / "images" / "a.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"png")
    tool = EditImageTool()
    assert tool._resolve_image("/api/batch-image/image/ImageBatch_1/a.png") == str(img.resolve())
    assert tool._resolve_image("http://127.0.0.1:5000/api/batch-image/image/ImageBatch_1/a.png") == str(img.resolve())
    assert tool._resolve_image("/api/batch-image/image/ImageBatch_1/..%2F..%2Fsecret") is None
    assert tool._resolve_image("/api/batch-image/image/ImageBatch_1/missing.png") is None


def test_status_can_wait_for_a_job_to_finish(monkeypatch):
    from backend.tools import image_tools

    answers = iter(["running", "running", "done"])
    monkeypatch.setattr(image_tools, "_http_json", lambda method, path, *a, **k: {
        "intent": "music", "status": next(answers),
        "result": {"path": "/x/song.wav", "document_id": 3}})
    monkeypatch.setattr(image_tools, "STATUS_POLL_S", 0)
    monkeypatch.setattr(image_tools.time, "sleep", lambda s: None)
    tool = image_tools.GenerationStatusTool()
    tool.set_context({"transport": "mcp"})
    res = tool.execute(batch_id="d" * 32, wait_seconds=30)
    assert res.success and "complete" in res.output and "Still running" not in res.output

    answers = iter(["running"])
    res = tool.execute(batch_id="d" * 32)
    assert "Still running" in res.output
