"""Reading and removing tool-call markup leaves the code in a reply alone."""
from backend.utils.tool_markup import brackets_to_xml, strip_tool_markup


def test_brackets_in_code_survive_the_bracket_call_form():
    reply = ("[tool_call][tool]edit_code[/tool][filepath]a.py[/filepath]"
             "[old_text]total = items[i] + cache[key][/old_text][new_text>x = [1, 2]</new_text>[/tool_call]")
    xml = brackets_to_xml(reply)
    assert "<old_text>total = items[i] + cache[key]</old_text>" in xml
    assert "<new_text>x = [1, 2]</new_text>" in xml
    assert xml.startswith("<tool_call><tool>edit_code</tool>")


def test_a_reply_with_no_closing_tags_is_left_as_it_is():
    assert brackets_to_xml("use items[i] here") == "use items[i] here"


def test_leaked_call_tags_go_and_code_stays():
    reply = ("Here is the pom:\n```xml\n<url>http://x</url><value>1</value>\n```\n"
             "Use <tooltip title='x'/> when a < b and c > d. <tool_call><tool>x</tool> [tool_call] List<String>")
    cleaned = strip_tool_markup(reply)
    assert "<url>http://x</url><value>1</value>" in cleaned
    assert "<tooltip title='x'/>" in cleaned
    assert "a < b and c > d" in cleaned
    assert "List<String>" in cleaned
    assert "tool_call" not in cleaned and "<tool>" not in cleaned
