"""Tests for natural-language / bare CLI routing in the REPL."""

from llx.intent_router import resolve_repl_line


class TestResolveReplLine:
    def test_agents_list(self):
        assert resolve_repl_line("agents list") == ("agents", ["list"])

    def test_guaardvark_prefix(self):
        assert resolve_repl_line("guaardvark agents list") == ("agents", ["list"])

    def test_nl_list_agents(self):
        assert resolve_repl_line("list agents") == ("agents", ["list"])

    def test_status(self):
        assert resolve_repl_line("status") == ("status", [])

    def test_system_status(self):
        assert resolve_repl_line("system status") == ("status", [])

    def test_health_check(self):
        assert resolve_repl_line("health check") == ("health", [])

    def test_run_agent(self):
        assert resolve_repl_line("run agent general assistant") == (
            "agents",
            ["run", "general assistant"],
        )

    def test_chat_passthrough(self):
        assert resolve_repl_line("explain this codebase") is None

    def test_slash_passthrough(self):
        assert resolve_repl_line("/agents list") is None

    def test_local_coding_intents(self):
        assert resolve_repl_line("read repl.py") == ("read", ["repl.py"])
        assert resolve_repl_line("grep TODO in cli") == ("grep", ["TODO in cli"])
        assert resolve_repl_line("ls cli/llx") == ("ls", ["cli/llx"])
        assert resolve_repl_line("edit foo.py fix the bug") == ("edit", ["foo.py fix the bug"])
        assert resolve_repl_line("run pytest") == ("run", ["pytest"])
        assert resolve_repl_line("todo add write tests") == ("todo", ["add write tests"])

    def test_image_generation_intents(self):
        assert resolve_repl_line("generate an image of the batmobile") == (
            "imagine",
            ["the batmobile"],
        )
        assert resolve_repl_line("create a picture of a sunset") == (
            "imagine",
            ["a sunset"],
        )
        assert resolve_repl_line("generate a video of waves") == (
            "video",
            ["waves"],
        )
        assert resolve_repl_line("generate csv report") == ("generate", ["csv", "report"])

    def test_plugin_and_gpu_intents(self):
        assert resolve_repl_line("list plugins") == ("plugins", ["list"])
        assert resolve_repl_line("gpu status") == ("gpu", ["status"])
        assert resolve_repl_line("what's using the gpu") == ("gpu", ["status"])
        assert resolve_repl_line("start comfyui") == ("plugins", ["start", "comfyui"])
