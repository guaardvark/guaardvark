"""Tool-call markup in model replies: reading the bracket form, and removing
leaked markup from text that is shown or saved.

Replies carry code. Both jobs here leave code alone: `items[i]` is not a
bracket tag, `<url>` inside a fenced pom.xml is not a tool parameter, and
`a < b and c > d` is not a tag at all.
"""
import re

_FENCE = re.compile(r"(```.*?(?:```|\Z))", re.DOTALL)

_CALL_TAGS = "tool_call|tool|observation"
# Parameter names the chat tools use, which a half-written call can leave behind.
_PARAM_TAGS = ("result|reasoning|query|url|param_name|parameter|value|full_page|selector|format|"
               "max_results|analysis_type|include_metadata")

# The name must end at the tag's end or an attribute: <tool> yes, <tooltip> no.
_XML_CALL = re.compile(rf"</?(?:{_CALL_TAGS})(?=[\s/>])[^>]*>")
_XML_CALL_AND_PARAMS = re.compile(rf"</?(?:{_CALL_TAGS}|{_PARAM_TAGS})(?=[\s/>])[^>]*>")
_BRACKET_CALL = re.compile(rf"\[/?(?:{_CALL_TAGS})\]")


def strip_tool_markup(text: str, *, params: bool = True) -> str:
    """``text`` without leaked tool-call tags. Fenced code blocks are returned
    as they are; outside them only the call tags (and, with ``params``, the
    known parameter tags) are removed, never other markup."""
    if not text:
        return text
    pattern = _XML_CALL_AND_PARAMS if params else _XML_CALL
    parts = _FENCE.split(text)
    for index in range(0, len(parts), 2):  # odd indexes are fenced blocks
        parts[index] = _BRACKET_CALL.sub("", pattern.sub("", parts[index]))
    return "".join(parts)


def brackets_to_xml(reply: str) -> str:
    """A reply written in the bracket call form (`[tool_call][tool]name[/tool]
    [path]x[/path][/tool_call]`), as the XML the parser reads.

    Models also mix the two forms ("[name>value</name>"). Only names that have
    a closing tag in the reply are converted, so an index or a list in a
    parameter's value (`items[i]`, `[1, 2]`) stays as written.
    """
    names = set(re.findall(r"[\[<]/(\w+)[\]>]", reply))
    if not names:
        return reply
    group = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    reply = re.sub(rf"\[/({group})[\]>]", r"</\1>", reply)
    reply = re.sub(rf"\[({group})[\]>]", r"<\1>", reply)
    return re.sub(rf"<(/?(?:{group}))\]", r"<\1>", reply)
