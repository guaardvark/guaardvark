import pytest
from backend.utils.code_chunker import CodeAwareChunker, CODE_LANGUAGE_MAP


class TestCodeLanguageMap:

    def test_python_extension(self):
        assert CODE_LANGUAGE_MAP.get(".py") == "python"

    def test_javascript_extension(self):
        assert CODE_LANGUAGE_MAP.get(".js") == "javascript"

    def test_typescript_extension(self):
        assert CODE_LANGUAGE_MAP.get(".ts") == "typescript"

    def test_tsx_extension(self):
        assert CODE_LANGUAGE_MAP.get(".tsx") == "typescript"

    def test_java_extension(self):
        assert CODE_LANGUAGE_MAP.get(".java") == "java"

    def test_unknown_extension_returns_none(self):
        assert CODE_LANGUAGE_MAP.get(".xyz") is None


class TestCodeAwareChunker:

    def test_is_code_file_true(self):
        chunker = CodeAwareChunker()
        assert chunker.is_code_file("app.py") is True
        assert chunker.is_code_file("index.js") is True
        assert chunker.is_code_file("Component.tsx") is True

    def test_is_code_file_false(self):
        chunker = CodeAwareChunker()
        assert chunker.is_code_file("readme.md") is False
        assert chunker.is_code_file("data.csv") is False
        assert chunker.is_code_file("image.png") is False

    def test_get_language_for_file(self):
        chunker = CodeAwareChunker()
        assert chunker.get_language("app.py") == "python"
        assert chunker.get_language("index.js") == "javascript"
        assert chunker.get_language("readme.md") is None

    def test_chunk_python_code_preserves_functions(self):
        chunker = CodeAwareChunker()
        python_code = '''import os
import sys

def hello():
    """Say hello."""
    print("hello world")

def goodbye():
    """Say goodbye."""
    print("goodbye world")

class Greeter:
    def greet(self, name):
        return f"Hello, {name}!"

    def farewell(self, name):
        return f"Goodbye, {name}!"
'''
        nodes = chunker.chunk_code(python_code, "python", "example.py")
        assert len(nodes) > 0
        for node in nodes:
            assert len(node.text.strip()) > 0
        # At least one node should contain a complete function
        texts = [n.text for n in nodes]
        has_hello = any("def hello" in t and "hello world" in t for t in texts)
        assert has_hello, "Expected a chunk containing the complete hello() function"

    def test_chunk_javascript_code(self):
        chunker = CodeAwareChunker()
        js_code = '''import React from 'react';

function App() {
    return <div>Hello</div>;
}

function Header() {
    return <h1>Title</h1>;
}

export default App;
'''
        nodes = chunker.chunk_code(js_code, "javascript", "App.jsx")
        assert len(nodes) > 0
        for node in nodes:
            assert len(node.text.strip()) > 0

    def test_chunk_adds_metadata(self):
        chunker = CodeAwareChunker()
        code = 'def foo():\n    return 42\n'
        nodes = chunker.chunk_code(code, "python", "foo.py")
        assert len(nodes) > 0
        meta = nodes[0].metadata
        assert meta.get("language") == "python"
        assert meta.get("file_path") == "foo.py"

    def test_fallback_on_unsupported_language(self):
        chunker = CodeAwareChunker()
        code = "some code in an exotic language\nline 2\nline 3\n"
        nodes = chunker.chunk_code(code, "unknown_lang", "file.xyz")
        assert len(nodes) > 0, "Fallback chunker should still produce nodes"
