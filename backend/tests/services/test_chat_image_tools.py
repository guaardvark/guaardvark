"""Chat image tools: edit backend picker, intercepts, consent, registration."""
from backend.tools.image_tools import EditImageTool, GenerateIdentityTool
from backend.services.unified_chat_engine import (
    GPU_HEAVY_TOOLS,
    _pin_image_edit_tools,
    attached_images_for_turn,
    pasted_image_prompt,
    pasted_images_note,
    identity_prompt_from_message,
    parse_outpaint_pad,
    user_wants_background_remove,
    user_wants_identity_generate,
    asks_for_person_in_photo,
    photo_person_prompt,
    user_wants_outpaint,
)


def test_pick_edit_backend_auto_prefers_qwen(monkeypatch):
    class FakeGen:
        def qwen_edit_installed(self):
            return True

        def _kontext_installed(self):
            return True

    monkeypatch.setattr(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator",
        lambda: FakeGen(),
    )
    assert EditImageTool._pick_edit_backend("auto") == "qwen"
    assert EditImageTool._pick_edit_backend("kontext") == "kontext"
    assert EditImageTool._pick_edit_backend("qwen-image-edit") == "qwen"
    assert EditImageTool._pick_edit_backend("zimage-turbo") == "img2img"


def test_pick_edit_backend_auto_falls_to_kontext(monkeypatch):
    class FakeGen:
        def qwen_edit_installed(self):
            return False

        def _kontext_installed(self):
            return True

    monkeypatch.setattr(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator",
        lambda: FakeGen(),
    )
    assert EditImageTool._pick_edit_backend("auto") == "kontext"


def test_identity_intent_does_not_steal_hat_on_person():
    assert user_wants_identity_generate("this person as a 1940s detective") is True
    assert user_wants_identity_generate("put this person in a rainy alley") is True
    assert user_wants_identity_generate("put a cowboy hat on this person") is False
    assert user_wants_identity_generate("same face, new scene") is True


def test_picture_request_pointing_at_the_photographed_person():
    assert asks_for_person_in_photo("Make an image of this man driving a convertible") is True
    assert asks_for_person_in_photo("make a picture of me as an astronaut") is True
    assert asks_for_person_in_photo("draw her in a red coat") is True
    assert asks_for_person_in_photo("draw me a cat") is False
    assert asks_for_person_in_photo("put a hat on this man") is False
    assert asks_for_person_in_photo("make an image of this woman's dress") is False
    assert asks_for_person_in_photo("describe this man") is False


def test_pointing_words_become_a_subject_for_the_face():
    assert photo_person_prompt("Make an image of this man driving") == "a man driving"
    assert photo_person_prompt("make a picture of me as an astronaut") == "a person as an astronaut"
    assert photo_person_prompt("draw her in a red coat") == "a person in a red coat"


def test_background_and_outpaint_intent():
    assert user_wants_background_remove("remove the background") is True
    assert user_wants_background_remove("transparent background please") is True
    assert user_wants_background_remove("remove the coffee cup") is False
    assert user_wants_outpaint("extend the canvas to the left") is True
    assert user_wants_outpaint("outpaint this photo") is True
    assert user_wants_outpaint("put a hat on him") is False


def test_parse_outpaint_pad_named_side():
    pad = parse_outpaint_pad("expand left")
    assert pad["left"] == 256
    assert pad["right"] == 0
    all_sides = parse_outpaint_pad("outpaint this")
    assert all_sides["left"] == all_sides["right"] == 256


def test_identity_prompt_strips_chrome():
    assert identity_prompt_from_message("this person as a 1940s detective") == "a 1940s detective"
    # A place alone renders an empty scene; the subject stays in front of it.
    assert (identity_prompt_from_message("Put this person into a sunlit greenhouse, same face.")
            == "a person in a sunlit greenhouse, same face")


def test_generate_identity_requires_a_consent_record(tmp_path, monkeypatch):
    result = GenerateIdentityTool().execute(prompt="a detective", consented=False)
    assert result.success is False
    assert "consented" in (result.error or "").lower()
    result_str = GenerateIdentityTool().execute(prompt="a detective", consented="false")
    assert result_str.success is False
    # consented=true is not proof: without the stored record the tool refuses
    # and says what is missing (the full gate is in test_consent_gate.py).
    import backend.services.consent_records as cr
    monkeypatch.setattr(cr, "_hash_dir", lambda: str(tmp_path / "consent"))
    face = tmp_path / "face.png"
    face.write_bytes(b"\x89PNG\r\n\x1a\n" + b"png-bytes")
    result = GenerateIdentityTool().execute(prompt="a detective", image=str(face), consented=True)
    assert result.success is False
    assert result.metadata["needs_consent"] is True
    assert result.metadata["reference_image"] == str(face)


def test_pin_image_edit_tools_when_attached():
    selected = _pin_image_edit_tools(
        True, ["web_search"],
        ["edit_image", "remove_background", "inpaint_image", "outpaint_image",
         "generate_identity", "web_search"],
    )
    assert selected[0] == "edit_image"
    assert "remove_background" in selected
    assert "generate_identity" in selected
    unchanged = _pin_image_edit_tools(False, ["web_search"], ["edit_image", "web_search"])
    assert unchanged == ["web_search"]


def test_gpu_heavy_includes_identity_not_rembg():
    assert "generate_identity" in GPU_HEAVY_TOOLS
    assert "inpaint_image" in GPU_HEAVY_TOOLS
    assert "outpaint_image" in GPU_HEAVY_TOOLS
    assert "remove_background" not in GPU_HEAVY_TOOLS


def test_named_image_tools_register(monkeypatch):
    from backend.tools.tool_registry_init import register_image_tools
    monkeypatch.delenv("GUAARDVARK_IDENTITY_TOOL", raising=False)
    names = register_image_tools()
    for n in ("edit_image", "remove_background", "inpaint_image", "outpaint_image", "generate_identity"):
        assert n in names  # identity is consent-gated in the tool, not by an env flag


def test_named_image_direct_identity_not_edit():
    from backend.services.unified_chat_engine import UnifiedChatEngine

    class FakeRegistry:
        def get_tool(self, name):
            return object() if name in (
                "generate_identity", "remove_background", "outpaint_image", "edit_image",
            ) else None

    engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
    engine.registry = FakeRegistry()
    engine._save_message = lambda *a, **k: None
    engine._image_data = None
    engine._calls = []

    def _run(tool, params, *a, **k):
        engine._calls.append((tool, params))
        return {"success": True, "tool": tool}

    engine._run_direct_tool_execution = _run
    engine._chat_image_source = lambda sid, message="": "/tmp/face.png"

    result = engine._try_named_image_direct(
        "this person as a 1940s detective", "s", lambda *a: None, "r", {},
    )
    assert result is not None
    assert engine._calls[0][0] == "generate_identity"
    assert "consented" not in engine._calls[0][1]  # the card decides, not the intercept

    engine._calls.clear()
    skipped = engine._try_named_image_direct(
        "put a cowboy hat on this person", "s", lambda *a: None, "r", {},
    )
    assert skipped is None


def test_named_image_tools_use_the_last_image_only_while_in_focus(monkeypatch, tmp_path):
    """After a plain chat turn the last image applies only when the message names it."""
    import backend.services.unified_chat_engine as uce
    from backend.tests.test_unified_chat_host_hooks import (
        _engine as chat_engine,
        _run as chat_turn,
    )

    sid = "sess-host"  # the session chat_turn runs in
    picture = tmp_path / "last.png"
    picture.write_bytes(b"png")

    class FakeRegistry:
        def get_tool(self, name):
            return object() if name == "remove_background" else None

    engine = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    engine.registry = FakeRegistry()
    engine._image_data = None
    calls = []
    engine._run_direct_tool_execution = (
        lambda tool, params, *a, **k: calls.append((tool, params)) or {"success": True}
    )

    def ask(message):
        return engine._try_named_image_direct(message, sid, lambda *a: None, "r", {})

    try:
        uce._remember_session_image(sid, str(picture))
        assert ask("remove the background") is not None
        assert calls == [("remove_background", {"image": str(picture)})]

        chat_turn(chat_engine(monkeypatch), "hello there, how are you today", {})
        assert ask("remove the background") is None
        assert len(calls) == 1

        assert ask("remove the background from the last image") is not None
        assert calls[-1] == ("remove_background", {"image": str(picture)})
    finally:
        uce._SESSION_LAST_EDIT.pop(sid, None)
        uce._SESSION_IMAGE_FOCUS.discard(sid)


# ── a busy GPU: chat waits, other callers are told at once ─────────────────

def test_chat_turns_wait_for_the_gpu_and_others_do_not(monkeypatch):
    from backend.services import agent_control_service as acs
    from backend.tools import image_tools

    acs.set_chat_stop_check(None)
    assert image_tools._chat_gpu_wait() is None
    try:
        acs.set_chat_stop_check(lambda: False)
        monkeypatch.setenv("GUAARDVARK_IMAGE_VRAM_WAIT_S", "120")
        wait = image_tools._chat_gpu_wait()
        assert wait["wait_s"] == 120.0 and wait["should_stop"]() is False
    finally:
        acs.set_chat_stop_check(None)


def test_gpu_refusals_read_as_try_again():
    from backend.services.gpu_resource_policy import GpuWaitStopped
    from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError
    from backend.tools.image_tools import _gpu_refusal

    busy = GpuBusyError("GPU is held by video_render:chat_qwen_edit_1234 — wait for completion")
    # The chat engine keeps the edit for a later "try again" when it sees either phrase.
    assert "try again" in _gpu_refusal(busy, None)
    waited = _gpu_refusal(busy, {"wait_s": 600})
    assert "10 minutes" in waited and "try again" in waited
    assert "Stopped" in _gpu_refusal(GpuWaitStopped("stopped"), {"wait_s": 600})
    assert _gpu_refusal(GpuCapacityError("does not fit"), {"wait_s": 600}) is None
    assert _gpu_refusal(RuntimeError("other"), None) is None


# ── the live camera frame is context, never the user's picture ────────────

CAMERA_FRAME = "Y2FtZXJhLWZyYW1l"  # base64 of b"camera-frame"


def _camera_turn_engine(monkeypatch, sees_images):
    """An engine whose photo intercepts are real; model, RAG and saves are recorded."""
    import types
    import backend.services.unified_chat_engine as uce
    import backend.utils.chat_utils as chat_utils
    import backend.utils.settings_utils as settings_utils

    class _Tool:
        requires_approval = False
        read_only = True
        observation_chars = 4000
        parameters = {}
        category = "test"

        def __init__(self, name):
            self.name = name
            self.description = f"{name} tool"

    class _Registry:
        def __init__(self, names):
            self._tools = {n: _Tool(n) for n in names}

        def list_tools(self):
            return list(self._tools)

        def get_tool(self, name):
            return self._tools.get(name)

        def get_tool_names(self):
            return list(self._tools)

        def as_ollama_tools(self, tool_names=None):
            return []

        def execute_tool(self, name, on_output=None, agent_context=None, **params):
            raise AssertionError(f"no tool should run on this turn, got {name}")

    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.registry = _Registry([
        "edit_image", "remove_background", "outpaint_image", "generate_identity", "web_search",
    ])
    e.llm = types.SimpleNamespace(model="test-model")
    e.max_iterations = 2
    e.calls = {"rag": [], "direct": [], "llm_messages": [], "saved": []}

    class _Selector:
        def select(self, message, registry):
            return ["web_search"]

    e._semantic_selector = _Selector()
    e._load_history = lambda session_id, limit=None: [
        {"role": "user", "content": "explain the plan"},
        {"role": "assistant", "content": "Here is a long explanation of the plan. " * 20},
    ]
    e._load_rules = lambda model_name: "ENGINE PERSONA"
    e._build_system_prompt = lambda *a, **k: "SYSTEM"
    e._get_routed_tools = lambda message: []
    e._retrieve_rag_context = lambda message: e.calls["rag"].append(message) or "KB PASSAGE"
    e._should_skip_rag = lambda message: False
    e._format_interface_context = lambda options: ""
    e._compact_history = lambda history, *a, **k: history
    e._analyze_pasted_image = lambda *a, **k: None
    e._warmup_chat_llm_async = lambda *a, **k: None
    e._maybe_summarize_session = lambda session_id: None
    e._save_message = lambda session_id, role, content, extra_data=None: e.calls["saved"].append(
        (role, extra_data))
    e._try_direct_tool = lambda *a, **k: None
    for name in (
        "_try_image_generate_retry", "_try_image_edit_retry", "_try_media_direct",
        "_try_music_video_direct", "_try_film_crew_direct",
        "_try_video_generate_direct", "_try_image_generate_direct",
    ):
        setattr(e, name, lambda *a, **k: None)

    def _direct(tool, params, *a, **k):
        e.calls["direct"].append(tool)
        return {"success": True, "tool": tool}

    e._run_direct_tool_execution = _direct

    def _llm(messages, emit_fn, session_id, emit_tokens=True, max_tokens=768, iteration=1):
        e.calls["llm_messages"].append(list(messages))
        return "Here is the shorter version.", 1, 1

    e._call_llm_streaming = _llm
    e._last_llm_call_meta = {}

    monkeypatch.setattr(uce, "is_aborted", lambda session_id: False)
    monkeypatch.setattr(uce, "match_workstation_direct", lambda message: None)
    monkeypatch.setattr(settings_utils, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(chat_utils, "is_vision_model", lambda model_name: sees_images)
    return e


def _camera_turn(e, message):
    options = {"camera_frame": CAMERA_FRAME, "think": False, "skip_memory_capture": True}
    return e.chat("sess-camera", message, options, lambda name, payload: None)


def test_camera_frame_is_not_an_edit_source_and_rag_runs(monkeypatch):
    from backend.utils.vision_context_utils import CAMERA_FRAME_NOTE

    e = _camera_turn_engine(monkeypatch, sees_images=True)

    result = _camera_turn(e, "make it shorter")

    assert result.get("success") is not False, result
    assert e.calls["direct"] == []
    assert e.calls["rag"] == ["make it shorter"]
    user_turn = next(m for m in reversed(e.calls["llm_messages"][0]) if m["role"] == "user")
    assert user_turn["images"] == [CAMERA_FRAME]
    assert CAMERA_FRAME_NOTE in user_turn["content"]
    assert ("user", None) in e.calls["saved"]


def test_camera_frame_is_not_sent_to_a_model_that_cannot_see(monkeypatch):
    e = _camera_turn_engine(monkeypatch, sees_images=False)

    _camera_turn(e, "make it shorter")

    assert e.calls["direct"] == []
    assert e.calls["rag"] == ["make it shorter"]
    assert all("images" not in m for m in e.calls["llm_messages"][0])


def test_api_sends_the_camera_frame_as_context_not_as_the_image(monkeypatch):
    import threading
    import types
    from flask import Flask

    import backend.api.unified_chat_api as api
    import backend.config as config
    import backend.services.unified_chat_engine as uce
    import backend.tools.tool_registry_init as tool_registry_init
    import backend.utils.vision_context_utils as vision_context_utils

    received = []

    class _Engine:
        def __init__(self, *a, **k):
            pass

        def chat(self, session_id, message, options, emit_fn, **kwargs):
            received.append((dict(options), kwargs))

    started = []

    class _Thread(threading.Thread):
        def start(self):
            started.append(self)
            super().start()

    monkeypatch.setattr(config, "AGENT_BRAIN_ENABLED", False)
    monkeypatch.setattr(tool_registry_init, "initialize_all_tools", lambda: object())
    monkeypatch.setattr(uce, "UnifiedChatEngine", _Engine)
    monkeypatch.setattr(vision_context_utils, "get_vision_context", lambda: {"is_active": True})
    monkeypatch.setattr(vision_context_utils, "get_latest_frame", lambda: CAMERA_FRAME)
    monkeypatch.setattr(api, "_inflight", {})
    monkeypatch.setattr(api, "threading", types.SimpleNamespace(
        Thread=_Thread, get_ident=threading.get_ident,
        current_thread=threading.current_thread, Lock=threading.Lock,
    ))

    app = Flask("test_camera_frame_api")
    app.config["LLAMA_INDEX_LLM"] = object()
    app.register_blueprint(api.unified_chat_bp)
    response = app.test_client().post("/api/chat/unified", json={
        "session_id": "sess-camera-api",
        "message": "make it shorter",
        "options": {"camera_frame": "sent-by-the-client"},
    })
    for thread in started:
        thread.join(timeout=10)

    assert response.status_code == 200
    assert len(received) == 1
    options, kwargs = received[0]
    assert options["camera_frame"] == CAMERA_FRAME
    assert kwargs["image_data"] is None
    assert kwargs["image_url"] is None


# ---- several pictures on one turn -------------------------------------------

def test_attached_images_default_to_the_primary_picture():
    assert attached_images_for_turn(None, {}) == []
    assert attached_images_for_turn("AAAA", {}) == ["AAAA"]
    assert attached_images_for_turn("AAAA", {"attached_images": "AAAA"}) == ["AAAA"]
    assert attached_images_for_turn("AAAA", {"attached_images": [None, ""]}) == ["AAAA"]


def test_attached_images_keep_order_with_the_primary_first():
    opts = {"attached_images": ["AAAA", "BBBB", "CCCC"]}
    assert attached_images_for_turn("AAAA", opts) == ["AAAA", "BBBB", "CCCC"]
    # A list that does not lead with the primary picture is reordered, not trusted.
    opts = {"attached_images": ["BBBB", "AAAA", "CCCC"]}
    assert attached_images_for_turn("AAAA", opts) == ["AAAA", "BBBB", "CCCC"]


def test_pasted_images_note_for_one_and_several():
    assert pasted_images_note([]) is None
    assert pasted_images_note([None, "  "]) is None
    assert pasted_images_note(["a red barn"]) == (
        "[The user pasted an image. Vision model analysis: a red barn]"
    )
    assert pasted_images_note(["a red barn", None, "a grey cat"]) == (
        "[The user pasted 3 images. Vision model analysis: "
        "Image 1 of 3: a red barn; Image 3 of 3: a grey cat]"
    )


def test_history_returns_every_picture_of_a_multi_picture_turn():
    """A reload shows all the pictures of a turn, not just the first: the
    history route flattens ``imageUrls`` out of extra_data like ``imageUrl``."""
    from flask import Flask

    from backend.api import enhanced_chat_api
    from backend.models import LLMMessage, LLMSession, db

    urls = ["/api/enhanced-chat/vision/image/a.png", "/api/enhanced-chat/vision/image/b.png"]
    app = Flask("test_history_multi_picture")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    app.register_blueprint(enhanced_chat_api.enhanced_chat_bp)
    with app.app_context():
        db.create_all()
        db.session.add(LLMSession(id="s-pics", user="default"))
        db.session.flush()
        db.session.add(LLMMessage(
            session_id="s-pics", role="user", content="compare these",
            extra_data={"hasImage": True, "imageUrl": urls[0], "imageUrls": urls,
                        "messageType": "image_upload"},
        ))
        db.session.commit()
        try:
            body = app.test_client().get("/api/enhanced-chat/s-pics/history").get_json()
        finally:
            db.session.remove()
            db.drop_all()

    message = body["messages"][0]
    assert message["imageUrl"] == urls[0]
    assert message["imageUrls"] == urls
    assert message["messageType"] == "image_upload"


def test_pasted_image_prompt_relays_a_question_but_not_the_default_message():
    assert pasted_image_prompt("what breed is this?") == (
        "Describe this image in detail. The user asks: what breed is this?"
    )
    plain = "Describe this image in detail. What do you see?"
    # The entry points' default messages for a picture without text, one or
    # several, carry no question for the guide dog.
    assert pasted_image_prompt("Describe this image.") == plain
    assert pasted_image_prompt("Describe these images.") == plain
    assert pasted_image_prompt("  describe these images.  ") == plain
    assert pasted_image_prompt("") == plain
    assert pasted_image_prompt(None) == plain
    # The composer's defaults name the files; they are not questions either.
    assert pasted_image_prompt("Describe this image: photo.png") == plain
    assert pasted_image_prompt("Describe these 2 images: a.png, b.png") == plain
    assert pasted_image_prompt("Describe these 10 images: a.png, b.png, c.png") == plain
    # A user's own words that merely start the same way are still relayed.
    asked = pasted_image_prompt("Describe this image: is the roof damaged?")
    assert asked.endswith("The user asks: Describe this image: is the roof damaged?")
    assert pasted_image_prompt("describe these images in one word").endswith(
        "The user asks: describe these images in one word"
    )


def test_stop_halts_the_descriptions_of_the_remaining_pictures(monkeypatch):
    """Up to four guide-dog calls run in sequence for a text-only model; Stop
    between them leaves the rest undescribed instead of running to the end."""
    import backend.services.unified_chat_engine as uce

    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    described = []

    def _describe(image, user_message):
        described.append(image)
        return f"description of {image}"

    e._analyze_pasted_image = _describe
    checks = []

    # Stop arrives once the first picture has been described.
    def _aborted(session_id):
        checks.append(session_id)
        return len(described) >= 1

    monkeypatch.setattr(uce, "is_aborted", _aborted)

    out = e._describe_attached_images(["AAAA", "BBBB", "CCCC"], "compare these", "sess-stop")

    assert described == ["AAAA"]
    assert out == ["description of AAAA", None, None]
    assert checks == ["sess-stop", "sess-stop"]
    # The note names what was described; the ReACT loop stops the turn next.
    assert uce.pasted_images_note(out) == (
        "[The user pasted 3 images. Vision model analysis: Image 1 of 3: description of AAAA]"
    )


def test_every_picture_is_described_when_the_turn_runs_on(monkeypatch):
    import backend.services.unified_chat_engine as uce

    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e._analyze_pasted_image = lambda image, user_message: f"description of {image}"
    monkeypatch.setattr(uce, "is_aborted", lambda session_id: False)

    out = e._describe_attached_images(["AAAA", "BBBB"], "compare these", "sess-run")

    assert out == ["description of AAAA", "description of BBBB"]


def test_api_keeps_picture_urls_aligned_when_one_save_fails(monkeypatch):
    """If the first picture cannot be saved, imageUrl is None, not the second
    picture's URL; the URL list keeps one slot per picture in attach order."""
    import threading
    import types
    from flask import Flask

    import backend.api.unified_chat_api as api
    import backend.config as config
    import backend.services.unified_chat_engine as uce
    import backend.tools.tool_registry_init as tool_registry_init

    received = []

    class _Engine:
        def __init__(self, *a, **k):
            pass

        def chat(self, session_id, message, options, emit_fn, **kwargs):
            received.append((dict(options), kwargs))

    started = []

    class _Thread(threading.Thread):
        def start(self):
            started.append(self)
            super().start()

    saved = []

    def _save(image_data):
        saved.append(image_data)
        return None if image_data == "AAAA" else f"/api/enhanced-chat/vision/image/{image_data}.png"

    monkeypatch.setattr(config, "AGENT_BRAIN_ENABLED", False)
    monkeypatch.setattr(tool_registry_init, "initialize_all_tools", lambda: object())
    monkeypatch.setattr(uce, "UnifiedChatEngine", _Engine)
    monkeypatch.setattr(api, "_save_chat_image", _save)
    monkeypatch.setattr(api, "_inflight", {})
    monkeypatch.setattr(api, "threading", types.SimpleNamespace(
        Thread=_Thread, get_ident=threading.get_ident,
        current_thread=threading.current_thread, Lock=threading.Lock,
    ))

    app = Flask("test_picture_urls_api")
    app.config["LLAMA_INDEX_LLM"] = object()
    app.register_blueprint(api.unified_chat_bp)
    response = app.test_client().post("/api/chat/unified", json={
        "session_id": "sess-pictures-api",
        "message": "compare these",
        "images": ["AAAA", "BBBB", "CCCC"],
    })
    for thread in started:
        thread.join(timeout=10)

    assert response.status_code == 200
    assert saved == ["AAAA", "BBBB", "CCCC"]
    assert len(received) == 1
    options, kwargs = received[0]
    assert kwargs["image_data"] == "AAAA"
    assert kwargs["image_url"] is None
    assert options["attached_images"] == ["AAAA", "BBBB", "CCCC"]
    assert options["attached_image_urls"] == [
        None,
        "/api/enhanced-chat/vision/image/BBBB.png",
        "/api/enhanced-chat/vision/image/CCCC.png",
    ]
