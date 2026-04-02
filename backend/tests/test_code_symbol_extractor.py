import pytest
from backend.utils.code_symbol_extractor import extract_symbols


class TestPythonSymbols:

    def test_extracts_function_names(self):
        code = "def hello():\n    pass\n\ndef world(x, y):\n    return x + y\n"
        symbols = extract_symbols(code, "python")
        names = [s["name"] for s in symbols if s["type"] == "function"]
        assert "hello" in names
        assert "world" in names

    def test_extracts_class_names(self):
        code = "class MyClass:\n    pass\n\nclass AnotherClass(Base):\n    pass\n"
        symbols = extract_symbols(code, "python")
        names = [s["name"] for s in symbols if s["type"] == "class"]
        assert "MyClass" in names
        assert "AnotherClass" in names

    def test_extracts_imports(self):
        code = "import os\nimport sys\nfrom pathlib import Path\nfrom typing import List, Dict\n"
        symbols = extract_symbols(code, "python")
        imports = [s for s in symbols if s["type"] == "import"]
        import_names = [s["name"] for s in imports]
        assert "os" in import_names
        assert "sys" in import_names
        assert "Path" in import_names

    def test_extracts_method_names(self):
        code = "class Foo:\n    def bar(self):\n        pass\n    def baz(self, x):\n        return x\n"
        symbols = extract_symbols(code, "python")
        methods = [s for s in symbols if s["type"] == "method"]
        assert len(methods) >= 2


class TestJavaScriptSymbols:

    def test_extracts_function_declarations(self):
        code = "function handleClick(e) {\n  console.log(e);\n}\n"
        symbols = extract_symbols(code, "javascript")
        names = [s["name"] for s in symbols if s["type"] == "function"]
        assert "handleClick" in names

    def test_extracts_const_arrow_functions(self):
        code = "const fetchData = async (url) => {\n  return fetch(url);\n};\n"
        symbols = extract_symbols(code, "javascript")
        names = [s["name"] for s in symbols]
        assert "fetchData" in names

    def test_extracts_class(self):
        code = "class App extends React.Component {\n  render() { return null; }\n}\n"
        symbols = extract_symbols(code, "javascript")
        names = [s["name"] for s in symbols if s["type"] == "class"]
        assert "App" in names

    def test_extracts_imports(self):
        code = "import React from 'react';\nimport { useState } from 'react';\n"
        symbols = extract_symbols(code, "javascript")
        imports = [s for s in symbols if s["type"] == "import"]
        assert len(imports) >= 1


class TestUnsupportedLanguage:

    def test_returns_empty_for_unknown(self):
        symbols = extract_symbols("some code", "unknown_language")
        assert symbols == []

    def test_returns_empty_for_empty_code(self):
        symbols = extract_symbols("", "python")
        assert symbols == []
