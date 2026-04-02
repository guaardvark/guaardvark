import pytest
from backend.utils.contextual_prepender import generate_chunk_context, prepend_context_to_nodes
from llama_index.core.schema import TextNode


class TestGenerateChunkContext:

    def test_basic_context(self):
        ctx = generate_chunk_context(
            file_path="src/app.py",
            repo_name="myrepo",
            language="python",
        )
        assert "[python]" in ctx
        assert "myrepo" in ctx
        assert "src/app.py" in ctx

    def test_with_symbol(self):
        ctx = generate_chunk_context(
            file_path="src/app.py",
            repo_name="myrepo",
            language="python",
            symbol_name="main",
            symbol_type="function",
        )
        assert "function" in ctx
        assert "`main`" in ctx

    def test_no_repo_name(self):
        ctx = generate_chunk_context(
            file_path="utils.js",
            repo_name=None,
            language="javascript",
        )
        assert "[javascript]" in ctx
        assert "utils.js" in ctx
        assert "Repository" not in ctx

    def test_ends_with_double_newline(self):
        ctx = generate_chunk_context(
            file_path="a.py", repo_name="r", language="python"
        )
        assert ctx.endswith("\n\n")


class TestPrependContextToNodes:

    def test_prepends_context_to_node_text(self):
        node = TextNode(
            text="def foo():\n    return 42\n",
            metadata={"language": "python", "file_path": "foo.py"},
        )
        prepend_context_to_nodes([node], repo_name="testrepo")
        assert node.text.startswith("[python]")
        assert "def foo" in node.text

    def test_preserves_original_text_in_metadata(self):
        original = "def foo():\n    return 42\n"
        node = TextNode(
            text=original,
            metadata={"language": "python", "file_path": "foo.py"},
        )
        prepend_context_to_nodes([node], repo_name="testrepo")
        assert node.metadata["original_text"] == original

    def test_skips_nodes_without_language(self):
        node = TextNode(text="some text", metadata={"file_path": "notes.txt"})
        original_text = node.text
        prepend_context_to_nodes([node], repo_name="testrepo")
        assert node.text == original_text
