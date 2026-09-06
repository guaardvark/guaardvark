"""backend/utils/path_guard: request-supplied path pieces stay under the chosen root."""

import os
from pathlib import Path

import pytest

from backend.utils.path_guard import PathEscapesRoot, contained, contained_path


def test_joins_under_root(tmp_path):
    assert contained_path(tmp_path, "a", "b.mp4") == str(tmp_path / "a" / "b.mp4")
    assert contained(tmp_path, "a") == tmp_path / "a"


def test_root_itself_is_allowed(tmp_path):
    assert contained_path(tmp_path) == str(tmp_path)
    assert contained_path(tmp_path, ".") == str(tmp_path)
    assert contained_path(str(tmp_path) + os.sep, "x") == str(tmp_path / "x")


def test_absolute_part_inside_root_is_allowed(tmp_path):
    inside = tmp_path / "sub" / "f.txt"
    assert contained_path(tmp_path, str(inside)) == str(inside)


@pytest.mark.parametrize("bad", ["../x", "a/../../x", "/etc/passwd", "../outside/y"])
def test_traversal_raises(tmp_path, bad):
    with pytest.raises(PathEscapesRoot):
        contained_path(tmp_path, bad)


def test_sibling_with_shared_prefix_is_rejected(tmp_path):
    root = tmp_path / "out"
    with pytest.raises(PathEscapesRoot):
        contained_path(root, "..", "outside", "z")
    with pytest.raises(PathEscapesRoot):
        contained_path(root, str(tmp_path / "out2" / "z"))


def test_escape_is_a_value_error(tmp_path):
    """Callers that still catch ValueError around the old relative_to idiom keep working."""
    with pytest.raises(ValueError):
        contained(tmp_path, "..")


def test_filesystem_root(tmp_path):
    assert contained_path("/", "etc", "hosts") == "/etc/hosts"
