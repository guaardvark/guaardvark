"""The identity tool's consent gate: a stored record, obtained through the card.

``generate_identity`` runs only when a ``.consent`` record exists for the
reference image (``backend.services.consent_records``). The chat's direct
path pauses on the consent card, writes the record on approval, and answers
with a refusal on decline; a caller's ``consented=true`` alone is never enough.
"""

import os
import shutil

import pytest

import backend.services.consent_records as cr
import backend.services.unified_chat_engine as uce
from backend.services.agent_tools import ToolResult
from backend.tools.image_tools import GenerateIdentityTool


PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def face(tmp_path, monkeypatch):
    """A reference image inside the install, with the hash index under tmp.

    ``STORAGE_DIR`` is pointed at ``tmp_path`` so the image counts as one this
    install owns — the real case, where attachments land under ``data/``.
    """
    monkeypatch.setattr(cr, "_hash_dir", lambda: str(tmp_path / "consent"))
    import backend.config as cfg
    monkeypatch.setattr(cfg, "OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr(cfg, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "UPLOAD_DIR", str(tmp_path / "uploads"))
    p = tmp_path / "face.png"
    p.write_bytes(PNG + b"face-bytes" * 16)
    return str(p)


class _FakeGen:
    calls = []

    def generate_with_identity(self, **kw):
        _FakeGen.calls.append(kw)
        with open(kw["output_path"], "wb") as f:
            f.write(b"png")
        return kw["output_path"]


@pytest.fixture
def fake_generator(monkeypatch):
    _FakeGen.calls = []
    monkeypatch.setattr(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator", _FakeGen,
    )
    return _FakeGen


# ── consent_records ────────────────────────────────────────────────────────

def test_record_is_a_sidecar_with_who_when_how(face):
    assert cr.has_consent(face) is False
    rec = cr.record_consent(face, "chat_approval", session_id="s1")
    assert cr.has_consent(face) is True
    stored = cr.consent_record(face)
    assert stored["source"] == "chat_approval"
    assert stored["session_id"] == "s1"
    assert stored["sha256"] == rec["sha256"]
    assert stored["recorded_at"]
    import os
    assert os.path.isfile(face + ".consent")


def test_same_photo_at_a_new_path_is_still_consented(face, tmp_path):
    """The chat writes each attachment to a fresh temp path; the hash copy covers it."""
    cr.record_consent(face, "chat_approval")
    again = tmp_path / "edit_src_other.png"
    shutil.copyfile(face, again)
    assert cr.has_consent(str(again)) is True
    different = tmp_path / "someone_else.png"
    different.write_bytes(PNG + b"other-bytes")
    assert cr.has_consent(str(different)) is False


# ── what the record may touch ──────────────────────────────────────────────

def test_an_image_outside_the_install_gets_no_sidecar_but_is_still_consented(
    face, tmp_path, monkeypatch
):
    """The reference path comes from a tool argument, so a sidecar is only ever
    written inside a directory this install owns. Consent still sticks, by hash."""
    outside = tmp_path.parent / "outside_the_install"
    outside.mkdir(exist_ok=True)
    photo = outside / "holiday.png"
    photo.write_bytes(PNG + b"my-own-photo" * 8)

    assert cr.owned_sidecar_path(str(photo)) is None
    rec = cr.record_consent(str(photo), "chat_approval", session_id="s9")

    assert not (outside / "holiday.png.consent").exists()
    assert cr.has_consent(str(photo)) is True
    assert cr.consent_record(str(photo))["session_id"] == "s9"
    assert (tmp_path / "consent" / f"{rec['sha256']}.consent").is_file()


def test_a_reference_that_is_not_an_image_is_refused(face, tmp_path):
    """No reading, hashing or indexing a file because a prompt named it.

    The local is deliberately not called ``secret``: CodeQL treats a variable
    with that name as a credential and traces it interprocedurally into
    record_consent's write and log sinks, which reports the guard being tested
    here as four clear-text-storage findings in production code.
    """
    not_an_image = tmp_path / "id_rsa"
    not_an_image.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n")

    assert cr.content_hash(str(not_an_image)) is None
    assert cr.has_consent(str(not_an_image)) is False
    with pytest.raises(ValueError):
        cr.record_consent(str(not_an_image), "chat_approval")
    assert not (tmp_path / "id_rsa.consent").exists()
    assert not (tmp_path / "consent").exists() or not list((tmp_path / "consent").iterdir())


def test_a_reference_that_is_not_a_regular_file_is_refused(face, tmp_path):
    """A FIFO must not be opened and waited on.

    The reference path is a tool argument, so a caller can name one. A blocking
    ``open`` on a FIFO with no writer never returns, which would hang the
    request rather than refuse it. The test asserts termination as much as the
    verdict: it fails by timing out if the guard is removed.
    """
    fifo = tmp_path / "pipe.png"
    try:
        os.mkfifo(fifo)
    except (AttributeError, NotImplementedError, OSError):
        pytest.skip("platform has no mkfifo")

    assert cr.has_consent(str(fifo)) is False
    assert cr.content_hash(str(fifo)) is None
    # Refused before the image check even runs: record_consent's own
    # os.path.isfile is already False for a FIFO, so this is FileNotFoundError
    # rather than the ValueError a real-but-not-an-image file gets.
    with pytest.raises((ValueError, OSError)):
        cr.record_consent(str(fifo), "chat_approval")
    assert not (tmp_path / "pipe.png.consent").exists()


def test_a_directory_named_as_a_reference_is_refused(tmp_path):
    a_dir = tmp_path / "looks_like.png"
    a_dir.mkdir()
    assert cr.has_consent(str(a_dir)) is False


def test_a_sidecar_cannot_be_aimed_out_of_the_install_with_dots(face, tmp_path):
    """abspath normalisation happens before the containment check."""
    sneaky = str(tmp_path / "sub" / ".." / ".." / "escaped.png")
    assert cr.owned_sidecar_path(sneaky) is None
    inside = str(tmp_path / "sub" / ".." / "face.png")
    assert cr.owned_sidecar_path(inside) == str(tmp_path / "face.png") + ".consent"


def test_missing_file_has_no_consent(tmp_path):
    assert cr.has_consent(str(tmp_path / "nope.png")) is False
    with pytest.raises(FileNotFoundError):
        cr.record_consent(str(tmp_path / "nope.png"), "ui_upload")


# ── the tool ───────────────────────────────────────────────────────────────

def test_tool_refuses_without_a_record_even_when_caller_says_consented(face, fake_generator):
    result = GenerateIdentityTool().execute(prompt="a detective", image=face, consented=True)
    assert result.success is False
    assert result.metadata["needs_consent"] is True
    assert result.metadata["reference_image"] == face
    assert "consent" in result.error.lower()
    assert fake_generator.calls == []


def test_tool_runs_with_a_record(face, fake_generator):
    cr.record_consent(face, "chat_approval")
    result = GenerateIdentityTool().execute(prompt="a detective", image=face)
    assert result.success is True, result.error
    assert result.metadata["backend"] == "pulid-flux"
    assert len(fake_generator.calls) == 1
    assert fake_generator.calls[0]["image_path"] == face


def test_tool_still_honours_an_explicit_consented_false(face, fake_generator):
    cr.record_consent(face, "chat_approval")
    result = GenerateIdentityTool().execute(prompt="a detective", image=face, consented=False)
    assert result.success is False
    assert "consented=false" in result.error
    assert fake_generator.calls == []


def test_tool_is_marked_for_the_consent_card():
    tool = GenerateIdentityTool()
    assert tool.requires_approval is True
    assert tool.consent_gate is True
    assert "right to use this person's likeness" in tool.approval_prompt


# ── the chat direct path ───────────────────────────────────────────────────

class _Registry:
    def __init__(self):
        self.tool = GenerateIdentityTool()
        self.executions = []

    def get_tool(self, name):
        return self.tool if name == "generate_identity" else None

    def execute_tool(self, name, **params):
        self.executions.append((name, params))
        return self.tool.execute(**params)


def _engine(monkeypatch):
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.registry = _Registry()
    e._image_data = None
    e.saved = []
    e._save_message = lambda sid, role, content, extra_data=None: e.saved.append((role, content, extra_data))
    monkeypatch.setattr(uce, "is_aborted", lambda session_id: False)
    monkeypatch.setattr(uce, "APPROVAL_TIMEOUT_S", 0.2)
    return e


def _run_direct(e, face, answer):
    """Drive the identity intercept; ``answer`` is what the card returns (None = silence)."""
    events = []

    def emit(name, payload):
        events.append((name, payload))
        if name == "chat:tool_approval_request" and answer is not None:
            uce.set_approval_response(payload["session_id"], answer)

    e._chat_image_source = lambda sid: face
    result = e._try_named_image_direct("this person as a 1940s detective", "sess-c", emit, "req-1", {})
    return result, events


def _card(events):
    cards = [p for n, p in events if n == "chat:tool_approval_request"]
    return cards[0] if cards else None


def test_direct_path_pauses_and_the_card_carries_the_consent_shape(face, fake_generator, monkeypatch):
    e = _engine(monkeypatch)
    result, events = _run_direct(e, face, True)
    card = _card(events)
    assert card is not None
    assert card["consent"] is True
    assert card["tools"] == ["generate_identity"]
    detail = card["tool_details"][0]
    assert detail["tool"] == "generate_identity"
    assert detail["consent"] is True
    assert detail["reference_image"] == face
    assert "likeness" in detail["approval_prompt"]
    assert detail["reasoning"] == detail["approval_prompt"]
    assert "consented" not in detail["params"]
    # The card came before the tool call, and the tool ran after approval.
    names = [n for n, _ in events]
    assert names.index("chat:tool_approval_request") < names.index("chat:tool_call")
    assert result["success"] is True
    assert len(e.registry.executions) == 1
    assert "consented" not in e.registry.executions[0][1]


def test_approval_writes_the_record_then_runs(face, fake_generator, monkeypatch):
    e = _engine(monkeypatch)
    assert cr.has_consent(face) is False
    _run_direct(e, face, True)
    rec = cr.consent_record(face)
    assert rec["source"] == "chat_approval"
    assert rec["session_id"] == "sess-c"
    assert len(fake_generator.calls) == 1


def test_decline_refuses_without_running(face, fake_generator, monkeypatch):
    e = _engine(monkeypatch)
    result, events = _run_direct(e, face, False)
    assert result["success"] is False
    assert "consent" in result["response"].lower()
    assert cr.has_consent(face) is False
    assert e.registry.executions == []
    assert fake_generator.calls == []
    names = [n for n, _ in events]
    assert "chat:tool_call" not in names
    tool_result = [p for n, p in events if n == "chat:tool_result"][0]
    assert tool_result["result"]["needs_consent"] is True
    assert tool_result["result"]["reference_image"] == face
    assert [p for n, p in events if n == "chat:complete"][0]["response"] == result["response"]
    assert e.saved[-1][0] == "assistant"


def test_silence_counts_as_decline(face, fake_generator, monkeypatch):
    e = _engine(monkeypatch)
    result, _ = _run_direct(e, face, None)
    assert result["success"] is False
    assert e.registry.executions == []


def test_a_recorded_photo_does_not_ask_again(face, fake_generator, monkeypatch):
    cr.record_consent(face, "chat_approval")
    e = _engine(monkeypatch)
    result, events = _run_direct(e, face, None)
    assert _card(events) is None
    assert result["success"] is True
    assert len(fake_generator.calls) == 1


# ── the slash mapping ──────────────────────────────────────────────────────

def test_identity_slash_no_longer_grants_consent():
    from backend.services.slash_command_executor import resolve_slash_direct_tool

    tool, params = resolve_slash_direct_tool({
        "slash_command": "identity",
        "slash_args": "a 1940s detective in the rain",
    })
    assert tool == "generate_identity"
    assert params == {"prompt": "a 1940s detective in the rain"}


def test_consent_detail_carries_a_served_url_for_chat_attachments():
    from backend.services.unified_chat_engine import _served_output_url
    assert _served_output_url("/x/outputs/edit_inputs/edit_src_ab12.png") == "/api/outputs/edit_inputs/edit_src_ab12.png"
    assert _served_output_url("/x/outputs/generated_images/a.png") == "/api/outputs/generated_images/a.png"
    assert _served_output_url("/tmp/elsewhere/a.png") is None
    assert _served_output_url(None) is None
