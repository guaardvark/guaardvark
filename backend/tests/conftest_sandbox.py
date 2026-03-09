"""Shared fixtures for self-improvement test suite."""
import os
import shutil
import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "sandbox_code")


@pytest.fixture
def sandbox_dir(tmp_path):
    """Create a temporary sandbox with copies of fixture files."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    # Copy all fixture files into sandbox
    for f in os.listdir(FIXTURES_DIR):
        src = os.path.join(FIXTURES_DIR, f)
        if os.path.isfile(src):
            shutil.copy2(src, sandbox / f)
    return sandbox


@pytest.fixture
def sandbox_file(sandbox_dir):
    """Create a single temporary Python file for simple tests."""
    p = sandbox_dir / "test_target.py"
    p.write_text('def hello():\n    return "world"\n')
    return p


def ollama_available():
    """Check if Ollama is running and has a model loaded."""
    try:
        import urllib.request
        import json
        resp = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        data = json.loads(resp.read())
        return len(data.get("models", [])) > 0
    except Exception:
        return False


requires_llm = pytest.mark.skipif(
    not ollama_available(),
    reason="Ollama not available or no models loaded"
)
