"""The outputs resource provider: pages, cursors, containment and large files."""

import os
from pathlib import Path

import mcp.types as mcp_types
import pytest

from backend.mcp import resources_adapter as ra
from backend.mcp.config import MCPConfig


def _make(root: Path, names):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)


def _collect(root: Path, page_size: int):
    seen, cursor = [], None
    while True:
        page, cursor = ra._list_page(root, cursor, page_size=page_size)
        seen += [p.relative_to(root).as_posix() for p in page]
        if cursor is None:
            return seen


def test_pages_return_every_file_once_in_a_stable_order(tmp_path):
    root = tmp_path.resolve()
    _make(root, ["z.txt", "b/d/e.txt", "a.txt", "b.txt", "b/c.txt", "f/g.txt",
                 ".progress_jobs/job.json", "Thumbs.db"])
    expected = ["a.txt", "b/c.txt", "b/d/e.txt", "b.txt", "f/g.txt", "z.txt"]
    for page_size in (1, 2, 4, 100):
        assert _collect(root, page_size) == expected


def test_more_files_than_the_old_listing_cap_are_all_reachable(tmp_path):
    root = tmp_path.resolve()
    _make(root, [f"batch/{i:04d}.png" for i in range(510)])
    assert len(_collect(root, ra.PAGE_SIZE)) == 510


@pytest.mark.parametrize("cursor", ["!!not-base64!!", "Li4vZXRjL3Bhc3N3ZA=="])  # the second is ../etc/passwd
def test_an_invalid_cursor_is_refused(tmp_path, cursor):
    with pytest.raises(ValueError):
        ra._list_page(tmp_path.resolve(), cursor)


def test_a_symlink_out_of_the_root_is_neither_listed_nor_readable(tmp_path_factory):
    root = tmp_path_factory.mktemp("outputs").resolve()
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.txt"
    outside.write_text("secret")
    os.symlink(outside, root / "link.txt")
    _make(root, ["real.txt"])

    assert _collect(root, 10) == ["real.txt"]
    with pytest.raises(FileNotFoundError):
        ra._read_contents(ra._uri_for(root / "link.txt", root), root, 1024)


def test_a_parent_escape_uri_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        ra._read_contents("guaardvark://outputs/..%2F..%2Fetc%2Fpasswd", tmp_path.resolve(), 1024)


def test_a_file_that_disappears_is_not_found(tmp_path):
    root = tmp_path.resolve()
    _make(root, ["gone.txt"])
    uri = ra._uri_for(root / "gone.txt", root)
    (root / "gone.txt").unlink()
    with pytest.raises(FileNotFoundError):
        ra._read_contents(uri, root, 1024)


def test_a_file_above_the_inline_limit_is_linked_not_embedded(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_URL", "http://127.0.0.1:5000")
    root = tmp_path.resolve()
    (root / "clips").mkdir()
    (root / "clips" / "big clip.mp4").write_bytes(b"\x00" * 4096)
    contents, size = ra._read_contents(ra._uri_for(root / "clips" / "big clip.mp4", root), root, 1024)
    assert size == 4096
    assert isinstance(contents, mcp_types.TextResourceContents)
    assert "http://127.0.0.1:5000/outputs/clips/big%20clip.mp4" in contents.text


def test_a_small_binary_file_is_embedded(tmp_path):
    root = tmp_path.resolve()
    (root / "tiny.png").write_bytes(b"\x89PNG")
    contents, _size = ra._read_contents(ra._uri_for(root / "tiny.png", root), root, 1024)
    assert isinstance(contents, mcp_types.BlobResourceContents)


@pytest.mark.asyncio
async def test_the_list_handler_pages_with_next_cursor(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    _make(root, [f"{i}.txt" for i in range(5)])
    monkeypatch.setattr(ra, "PAGE_SIZE", 2)
    cfg = MCPConfig()
    cfg.resources.outputs_root = str(root)
    on_list, _on_read, _count = ra.build_resource_handlers(cfg)

    names, cursor = [], None
    while True:
        page = await on_list(None, mcp_types.PaginatedRequestParams(cursor=cursor))
        assert len(page.resources) <= 2
        names += [r.name for r in page.resources]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert names == [f"{i}.txt" for i in range(5)]
