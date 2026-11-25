"""The image, video, film and music tools apply the shared media-input rules.

Over MCP (in the MCP process, or in the backend for a call the MCP server
forwarded) only uploads and the outputs folders MCP resources serve are read;
chat keeps any existing path; credential-named files are refused everywhere. No GPU, network or database:
every call here stops at input resolution or at a stubbed model preflight.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.tests._mcp_sdk import use_mcp_sdk

use_mcp_sdk()  # these tools reach the adapter, which imports mcp.types; see backend/tests/_mcp_sdk.py
from flask import Flask

from backend import config
from backend.mcp import config as mcp_config
from backend.utils import backend_http, media_inputs as mi
from backend.utils.backend_http import calls_for_mcp_client, is_mcp_caller


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "install"
    uploads, outputs = root / "data" / "uploads", root / "data" / "outputs"
    batch = uploads / "Images" / "ImageBatch_1" / "images" / "a.png"
    for p in (batch, outputs / "generated_images" / "edit_1.png"):
        p.parent.mkdir(parents=True)
        p.write_bytes(b"png")
    (root / ".env").write_text("KEY=not-a-secret")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"png")
    export = outputs / "chat-exports" / "session_1" / "index.json"
    export.parent.mkdir(parents=True)
    export.write_text('{"title": "a private conversation"}\n')
    policy = mcp_config.MCPConfig()
    monkeypatch.setattr(mcp_config, "load_config", lambda: policy)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", root)
    monkeypatch.setattr(mi, "resources_root", lambda: str(outputs.resolve()))
    return SimpleNamespace(root=root, uploads=uploads, outputs=outputs, outside=outside, env=root / ".env")


# ---- the forwarding mark ------------------------------------------------------------------
def test_is_mcp_caller_follows_the_transport_and_the_forwarding_mark():
    in_mcp = SimpleNamespace(_context={"transport": "mcp"})
    in_backend = SimpleNamespace(_context={})
    assert is_mcp_caller(in_mcp) and not is_mcp_caller(in_backend)
    with calls_for_mcp_client(True):
        assert is_mcp_caller(in_backend)
        with calls_for_mcp_client(False):
            assert is_mcp_caller(in_backend)
    assert not is_mcp_caller(in_backend)


def test_run_tool_in_backend_marks_the_call_as_from_mcp(monkeypatch):
    sent = {}

    def fake_request_json(method, path, *, payload=None, read_timeout=None, **kw):
        sent.update(payload)
        return backend_http.BackendResponse(200, {"result": {"success": True}}, None)

    monkeypatch.setattr(backend_http, "request_json", fake_request_json)
    backend_http.run_tool_in_backend("generate_video", {"prompt": "x"})
    assert sent[backend_http.CALLER_TRANSPORT_FIELD] == "mcp"


def test_tools_execute_runs_forwarded_calls_as_mcp_calls():
    from backend.api.tools_api import tools_bp
    from backend.services.agent_tools import ToolResult

    seen = []

    class Registry:
        def get_tool(self, name):
            return SimpleNamespace(parameters={})

        def execute_tool(self, name, **params):
            seen.append(is_mcp_caller(SimpleNamespace(_context={})))
            return ToolResult(success=True, output="ok")

    app = Flask(__name__)
    app.register_blueprint(tools_bp)
    app.tool_registry = Registry()
    with app.test_client() as client:
        client.post("/api/tools/execute", json={"tool_name": "t", "parameters": {}, "caller_transport": "mcp"})
        client.post("/api/tools/execute", json={"tool_name": "t", "parameters": {}})
    assert seen == [True, False]


# ---- edit family (the MCP server forwards it; the backend checks the inputs) ---------------
def _tool(cls, transport):
    tool = cls()
    tool.set_context({"transport": "mcp"} if transport == "mcp" else {})
    return tool


def test_edit_family_refuses_files_outside_uploads_and_outputs_over_mcp(tree):
    from backend.tools import image_tools as it

    # As the backend runs a call the MCP server forwarded: the inputs are
    # refused in the request, before any tool job starts.
    with calls_for_mcp_client(True):
        res = _tool(it.RemoveBackgroundTool, "backend").execute(image=str(tree.outside))
        assert not res.success and "MCP resources serve" in res.error
        res = _tool(it.InpaintImageTool, "backend").execute(
            instruction="x", image="/api/outputs/../../../outside.png")
        assert not res.success and "leaves the outputs folder" in res.error
        res = _tool(it.OutpaintImageTool, "backend").execute(image=str(tree.outside))
        assert not res.success and "MCP resources serve" in res.error
        edit = _tool(it.EditImageTool, "backend")
        res = edit.execute(instruction="x", image="/api/batch-image/image/ImageBatch_1/a.png",
                           reference_image_2=str(tree.outside))
        assert not res.success and "reference_image_2" in res.error


def test_edit_family_accepts_resource_uris_and_keeps_chat_paths(tree):
    from backend.tools import image_tools as it

    mcp_edit = it._edit_tool_for(_tool(it.InpaintImageTool, "mcp"))
    assert mcp_edit._context == {"transport": "mcp"}
    uri = mcp_edit._resolve_image_ref("guaardvark://outputs/generated_images/edit_1.png")
    assert uri.path and uri.path.endswith("edit_1.png")
    chat_edit = _tool(it.EditImageTool, "chat")
    assert chat_edit._resolve_image(str(tree.outside)) == str(tree.outside)
    assert chat_edit._resolve_image(str(tree.env)) is None
    assert chat_edit._resolve_image("/api/outputs/../../../outside.png") is None


# ---- generate_video (runs in the backend) -------------------------------------------------
@pytest.fixture
def video(monkeypatch):
    from backend.tools import image_tools as it
    import backend.services.video_model_registry as registry

    preflights = []
    monkeypatch.setattr(it.VideoGeneratorTool, "resolve_request",
                        staticmethod(lambda prompt, **kw: ({"model": "m", "duration_frames": 49, "metadata": {}}, None)))
    monkeypatch.setattr(registry, "prepare_video_model",
                        lambda model_id: preflights.append(model_id) or (False, "stub preflight"))
    return it.VideoGeneratorTool(), preflights


def test_generate_video_forwarded_from_mcp_reads_only_uploads_and_outputs(tree, video):
    tool, preflights = video
    with calls_for_mcp_client(True):
        res = tool.execute(prompt="push in", first_image=str(tree.outside))
        assert not res.success and "MCP resources serve" in res.error and not preflights
        res = tool.execute(prompt="push in", first_image="/api/batch-image/image/ImageBatch_1/a.png",
                           last_image="guaardvark://outputs/generated_images/edit_1.png")
        assert "stub preflight" in res.error and preflights == ["m"]


def test_generate_video_in_chat_keeps_paths_but_refuses_credentials(tree, video):
    tool, preflights = video
    res = tool.execute(prompt="push in", first_image=str(tree.outside))
    assert "stub preflight" in res.error
    res = tool.execute(prompt="push in", reference_images=[str(tree.env)])
    assert not res.success and "keys or credentials" in res.error and len(preflights) == 1


# ---- start_film_crew / generate_music_video script and song inputs -------------------------
def test_script_files_are_text_only_capped_and_confined(tree):
    from backend.tools import video_pipeline_tools as vpt

    script = tree.uploads / "script.txt"
    script.write_text("INT. KITCHEN - NIGHT\nA fox steals a pie.\n")
    assert vpt._script_body(str(script), mcp=True) == (script.read_text(), None)
    binary = tree.uploads / "script.pdf"
    binary.write_bytes(b"%PDF-1.7\n\x00\x01\xff")
    assert "not a text file" in vpt._script_body(str(binary))[1]
    latin = tree.uploads / "latin1.txt"
    latin.write_bytes("Caf\xe9".encode("latin-1"))
    assert "not UTF-8" in vpt._script_body(str(latin))[1]
    big = tree.uploads / "big.txt"
    with open(big, "wb") as fh:
        fh.truncate(vpt.SCRIPT_FILE_MAX_BYTES + 1)
    assert "read up to 1 MB" in vpt._script_body(str(big))[1]
    for mcp in (True, False):
        body, err = vpt._script_body(str(tree.env), mcp=mcp)
        assert body is None and "keys or credentials" in err
    body, err = vpt._script_body(str(tree.outside), mcp=True)
    assert body is None and "MCP resources serve" in err and "screenplay itself" in err


def test_a_one_line_script_that_names_no_file_is_the_script(tree):
    from backend.tools import video_pipeline_tools as vpt

    assert vpt._script_body("A fox steals a pie at midnight", mcp=True) == ("A fox steals a pie at midnight", None)
    body, err = vpt._script_body("guaardvark://outputs/missing.txt", mcp=True)
    assert body is None and "not found" in err


def test_song_refs_refuse_credentials_and_outside_files_before_any_database_use(tree):
    from backend.tools import video_pipeline_tools as vpt

    doc, err = vpt._document_from_song_ref(str(tree.env))
    assert doc is None and "keys or credentials" in err
    doc, err = vpt._document_from_song_ref(str(tree.outside), mcp=True)
    assert doc is None and "MCP resources serve" in err
    doc, err = vpt._document_from_song_ref(str(tree.outside))
    assert doc is None and "install root" in err


EXPORT = "chat-exports/session_1/index.json"


def _forms(tree, rel):
    return [str(tree.outputs / rel), f"/api/outputs/{rel}", f"guaardvark://outputs/{rel}"]


def test_film_and_music_refuse_unserved_outputs_over_mcp(tree):
    from backend.tools import video_pipeline_tools as vpt

    for ref in _forms(tree, EXPORT):
        body, err = vpt._script_body(ref, mcp=True)
        assert body is None and "MCP resources serve" in err, ref
        doc, err = vpt._document_from_song_ref(ref, mcp=True)
        assert doc is None and "MCP resources serve" in err, ref
        body, err = vpt._script_body(ref, mcp=False)
        assert err is None and "private conversation" in body, ref


def test_served_outputs_stay_accepted_over_mcp(tree):
    from backend.tools import video_pipeline_tools as vpt

    scene = tree.outputs / "files" / "scene.txt"
    scene.parent.mkdir(parents=True)
    scene.write_text("INT. ROOM.\nHi.\n")
    song = tree.outputs / "audio" / "song.wav"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"RIFF")
    for ref in _forms(tree, "files/scene.txt"):
        assert vpt._script_body(ref, mcp=True) == (scene.read_text(), None), ref
    for ref in _forms(tree, "audio/song.wav"):
        found = mi.resolve_media_ref(ref, mcp=True, label="song file", within_install=True)
        assert found.path == str(song.resolve()), ref
    for ref in _forms(tree, "generated_images/edit_1.png"):
        assert mi.resolve_media_ref(ref, mcp=True).path, ref
