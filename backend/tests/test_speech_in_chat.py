"""Speech in chat: when the speech tool is offered, and what plan mode says
about audio when chat has speech but not music."""
import pytest

from backend.services import plan_mode_data as pmd
from backend.services.unified_chat_engine import user_wants_speech


@pytest.mark.parametrize("message", [
    "Say 'good morning' out loud.",
    "Read this sentence aloud: Welcome to Guaardvark.",
    "Turn this paragraph into text to speech",
    "Narrate the intro for my video",
    "Do a voiceover for this script",
    "speak this: hello there",
])
def test_speech_requests_offer_the_tool(message):
    assert user_wants_speech(message)


@pytest.mark.parametrize("message", [
    "How do I turn on text to speech?",
    "Write a speech for my wedding",
    "What did she say?",
    "Make a 30-second lo-fi beat.",
    "I was laughing out loud at that",
    "just thinking out loud here",
    "",
])
def test_other_messages_do_not(message):
    assert not user_wants_speech(message)


def _chat_tools(*, speech: bool, music: bool):
    tools = {t for spec in pmd.STEP_KINDS.values() for t in spec["tools"]}
    if not speech:
        tools.discard("generate_speech")
    if not music:
        tools.discard("generate_music")
    return tools


def test_chat_with_speech_offers_speech_and_sends_music_elsewhere():
    available = _chat_tools(speech=True, music=False)
    assert "speech" in pmd.abilities(available)
    assert "music and speech" not in pmd.abilities(available)
    missing = pmd.unavailable(available)
    assert "music (that is done on the Audio Studio page)" in missing
    assert "speech" not in missing


def test_all_audio_tools_read_as_before():
    available = _chat_tools(speech=True, music=True)
    assert "music and speech" in pmd.abilities(available)
    assert "music" not in pmd.unavailable(available)


def test_no_audio_tools_read_as_before():
    available = _chat_tools(speech=False, music=False)
    assert "music and speech (that is done on the Audio Studio page)" in pmd.unavailable(available)
