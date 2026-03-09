"""Tests for LLM-powered code generation. Requires Ollama."""
import os
import sys
import py_compile
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tests.conftest_sandbox import requires_llm

# CodeGeneratorTool writes to OUTPUT_DIR/code/<filename>
from backend.config import OUTPUT_DIR
OUTPUT_CODE_DIR = os.path.join(OUTPUT_DIR, "code")


def _cleanup_generated(filename):
    """Remove generated file after test."""
    path = os.path.join(OUTPUT_CODE_DIR, filename)
    if os.path.exists(path):
        os.remove(path)


@requires_llm
class TestCodeGeneration:
    """Tests for CodeGeneratorTool — backend/tools/code_tools.py:17"""

    @pytest.mark.timeout(120)
    def test_generate_python_function(self):
        """Generate a Python function and verify it compiles."""
        filename = "test_gen_fibonacci.py"
        try:
            from backend.tools.code_tools import CodeGeneratorTool
            tool = CodeGeneratorTool()
            result = tool.execute(
                output_filename=filename,
                instructions="Write a Python function called 'fibonacci' that takes an integer n and returns the nth Fibonacci number using iteration. Include a docstring.",
                language="python"
            )
            assert result.success, f"Codegen failed: {result.error}"
            output_path = os.path.join(OUTPUT_CODE_DIR, filename)
            assert os.path.exists(output_path), f"Generated file not created at {output_path}"
            # Verify it compiles
            py_compile.compile(output_path, doraise=True)
            # Verify content contains the function
            content = open(output_path).read()
            assert "fibonacci" in content
        finally:
            _cleanup_generated(filename)

    @pytest.mark.timeout(120)
    def test_generate_valid_syntax(self):
        """Generate code and verify syntax is valid Python."""
        filename = "test_gen_stack.py"
        try:
            from backend.tools.code_tools import CodeGeneratorTool
            tool = CodeGeneratorTool()
            result = tool.execute(
                output_filename=filename,
                instructions="Write a Python class called 'Stack' with push, pop, and peek methods. Use a list internally.",
                language="python"
            )
            assert result.success, f"Codegen failed: {result.error}"
            output_path = os.path.join(OUTPUT_CODE_DIR, filename)
            content = open(output_path).read()
            compile(content, filename, "exec")  # Raises SyntaxError if invalid
            assert "class Stack" in content
        finally:
            _cleanup_generated(filename)

    @pytest.mark.timeout(120)
    def test_modify_existing_file(self, tmp_path):
        """Modify an existing file with instructions."""
        filename = "test_gen_modified.py"
        # Create input file
        input_file = tmp_path / "to_modify.py"
        input_file.write_text("def greet(name):\n    return f'Hello, {name}!'\n")

        try:
            from backend.tools.code_tools import CodeGeneratorTool
            tool = CodeGeneratorTool()
            result = tool.execute(
                input_file=str(input_file),
                output_filename=filename,
                instructions="Add a function called 'farewell' that takes a name and returns 'Goodbye, {name}!'. Keep the existing greet function.",
                language="python",
                preserve_structure=True
            )
            assert result.success, f"Codegen failed: {result.error}"
            output_path = os.path.join(OUTPUT_CODE_DIR, filename)
            content = open(output_path).read()
            assert "farewell" in content
            assert "greet" in content  # Original should be preserved
        finally:
            _cleanup_generated(filename)
