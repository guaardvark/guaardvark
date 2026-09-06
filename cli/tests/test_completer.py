"""Tab completion: slash and bare command prefixes, plus did-you-mean."""

from prompt_toolkit.document import Document

from llx.command_catalog import COMMAND_TREE, suggest_command
from llx.completer import SlashCompleter


def _names(text: str) -> list[str]:
    completer = SlashCompleter()
    return [c.text for c in completer.get_completions(Document(text), None)]


class TestSlashAndBareCompletion:
    def test_slash_prefix_completes_imagine(self):
        names = _names("/ima")
        assert "imagine" in names

    def test_bare_prefix_completes_imagine(self):
        names = _names("ima")
        assert "imagine" in names

    def test_empty_bare_line_does_not_dump_commands(self):
        assert _names("") == []

    def test_slash_alone_lists_catalog(self):
        names = _names("/")
        assert "imagine" in names
        assert "status" in names
        assert "recipes" in names
        assert set(names) == set(COMMAND_TREE)

    def test_subcommand_completion(self):
        names = _names("/jobs ")
        assert "list" in names
        assert "watch" in names

    def test_bare_subcommand_completion(self):
        names = _names("recipes ")
        assert "list" in names
        assert "validate" in names

    def test_at_mention_completes_paths(self, tmp_path, monkeypatch):
        (tmp_path / "README.md").write_text("x")
        monkeypatch.chdir(tmp_path)
        names = _names("@READ")
        assert any(n.startswith("README") for n in names)


class TestDidYouMean:
    def test_images_suggests_imagine(self):
        matches = suggest_command("imagn")
        assert "imagine" in matches

    def test_unknown_garbage_returns_empty_or_unrelated(self):
        matches = suggest_command("zzzzzzzzzzzz")
        assert matches == []
