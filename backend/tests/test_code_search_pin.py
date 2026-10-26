"""Questions about the source keep search_codebase in the prompt."""
import backend.services.unified_chat_engine as uce

ALL = ["search_knowledge_base", "search_codebase", "read_code", "generate_image", "map_codebase"]


def test_code_question_pins_search_and_read():
    out = uce._pin_code_search_tools(
        "Where in the backend is the decision made whether a model supports thinking?",
        ["search_knowledge_base"], ALL,
    )
    assert out[:2] == ["search_codebase", "read_code"]
    assert out[-1] == "search_knowledge_base"


def test_non_code_question_is_left_alone():
    out = uce._pin_code_search_tools("write a poem about the sea", ["generate_image"], ALL)
    assert out == ["generate_image"]


def test_pin_does_not_duplicate_or_invent_tools():
    out = uce._pin_code_search_tools("which function cuts the text", ["search_codebase"], ["search_codebase"])
    assert out == ["search_codebase"]


def test_observation_budget_comes_from_the_tool():
    class Wide:
        observation_chars = 4000

    class Narrow:
        pass

    assert int(getattr(Wide(), "observation_chars", 500) or 500) == 4000
    assert int(getattr(Narrow(), "observation_chars", 500) or 500) == 500


class _TC:
    def __init__(self, name, params=None):
        self.tool_name = name; self.parameters = params or {}


def test_signature_form_call_is_normalised():
    tc = _TC("search_codebase(query:string='per-chat think', limit:int?=5)")
    uce._normalize_signature_tool_call(tc)
    assert tc.tool_name == "search_codebase"
    assert tc.parameters == {"query": "per-chat think", "limit": "5"}


def test_existing_parameters_win_and_plain_names_are_untouched():
    tc = _TC('read_code(filepath:string="a.py")', {"filepath": "b.py"})
    uce._normalize_signature_tool_call(tc)
    assert tc.tool_name == "read_code" and tc.parameters == {"filepath": "b.py"}
    plain = _TC("search_codebase", {"query": "x"})
    uce._normalize_signature_tool_call(plain)
    assert plain.tool_name == "search_codebase" and plain.parameters == {"query": "x"}


def test_asks_about_code_matches_source_questions_only():
    assert uce._asks_about_code("Which module enforces that paths stay inside their directory?")
    assert uce._asks_about_code("how does the backend pick a video model")
    assert not uce._asks_about_code("make me a CSV of the eight workspaces")
    assert "search_codebase" in uce._code_search_nudge()


def test_a_keyword_inside_another_word_pins_nothing():
    out = uce._pin_code_search_tools("my wifi router keeps dropping", ["web_search"], ALL)
    assert out == ["web_search"]
    assert not uce._asks_about_code("is a subclass of mammals a reptile")
    assert not uce._asks_about_code("what does undefined behaviour mean in law")


def test_whole_code_keywords_still_pin():
    out = uce._pin_code_search_tools("where is the route for /api/x", ["web_search"], ALL)
    assert out[:2] == ["search_codebase", "read_code"]
    assert uce._asks_about_code("what does backend/app.py do on startup")
    assert uce._asks_about_code("list the routes the server registers")
    assert uce._asks_about_code("show me class Worker and its def run")
