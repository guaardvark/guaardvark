"""File management commands — list, upload, download, delete, mkdir."""

from pathlib import Path
import typer
from rich.tree import Tree

from llx.client import get_client, LlxError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console
from llx import output

console = make_console()
files_app = typer.Typer(help="File and folder management", no_args_is_help=True)


@files_app.command("list")
def files_list(
    path: str = typer.Option("/", "--path", "-p", help="Folder path to browse"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List files and folders."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/files/browse", path=path)
        result = data.get("data", data)
        folders = result.get("folders", [])
        documents = result.get("documents", [])

        if json_out or output.is_pipe():
            output.print_json(
                {"status": "success", "data": {"folders": folders, "documents": documents}}
            )
            return

        if not folders and not documents:
            output.print_warning(f"Empty directory: {path}")
            return

        tree = Tree(f"[bold]{path}[/bold]")
        for f in folders:
            name = f.get("name", f) if isinstance(f, dict) else str(f)
            tree.add(f"[llx.tree.folder]{name}/[/llx.tree.folder]")
        for d in documents:
            name = d.get("filename", d.get("name", str(d)))
            doc_id = d.get("id", "")
            size = d.get("size", 0)
            size_str = _format_size(size) if size else ""
            tree.add(f"[llx.tree.file]{name}[/llx.tree.file]  [llx.tree.meta]{size_str}  id:{doc_id}[/llx.tree.meta]")
        console.print(tree)

    except LlxError as e:
        raise output.fail(e)


@files_app.command("upload")
def files_upload(
    file_path: Path = typer.Argument(..., help="File to upload", exists=True),
    folder: str = typer.Option(None, "--folder", "-f", help="Target folder path"),
    tags: str = typer.Option(None, "--tags", "-t", help="Comma-separated tags"),
    index: bool = typer.Option(True, "--index/--no-index", help="Trigger indexing after upload (default: on)"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Upload a file. Indexing is triggered by default for RAG."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        if json_out or output.is_pipe():
            data = client.upload(
                "/api/files/upload",
                file_path,
                folder_path=folder or "",
                tags=tags or "",
                auto_index=str(index).lower(),
            )
        else:
            data = client.upload_with_progress(
                "/api/files/upload",
                file_path,
                console=console,
                folder_path=folder or "",
                tags=tags or "",
                auto_index=str(index).lower(),
            )
        result = data.get("data", data)
        doc_id = result.get("id")

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": result})
            return

        msg = f"Uploaded: {file_path.name} (id: {doc_id})"
        if index and doc_id:
            msg += " — indexing in progress"
        output.print_success(msg)

    except LlxError as e:
        raise output.fail(e)


@files_app.command("download")
def files_download(
    doc_id: int = typer.Argument(..., help="Document ID to download"),
    dest: Path = typer.Option(Path("."), "--dest", "-d", help="Destination directory"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
):
    """Download a file by ID."""
    server = server or get_global_server()
    try:
        client = get_client(server)
        info = client.get(f"/api/files/document/{doc_id}")
        doc_data = info.get("data", info)
        # The name comes from the server; keep it inside the destination.
        filename = Path(str(doc_data.get("filename") or "")).name or f"document_{doc_id}"
        dest_file = dest / filename
        client.download(f"/api/files/document/{doc_id}/download", dest_file)
        output.print_success(f"Downloaded: {dest_file}")
    except LlxError as e:
        raise output.fail(e)


@files_app.command("delete")
def files_delete(
    doc_id: int = typer.Argument(..., help="Document ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
):
    """Delete a file by ID."""
    server = server or get_global_server()
    if not force:
        typer.confirm(f"Delete document {doc_id}?", abort=True)
    try:
        client = get_client(server)
        client.delete(f"/api/files/document/{doc_id}")
        output.print_success(f"Deleted document {doc_id}")
    except LlxError as e:
        raise output.fail(e)


@files_app.command("mkdir")
def files_mkdir(
    name: str = typer.Argument(..., help="Folder name"),
    parent: str = typer.Option("", "--parent", "-p", help="Parent folder path, e.g. research/2026"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Create a folder."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.post("/api/files/folder", json={"name": name, "parent_path": parent or ""})
        result = data.get("data", data)
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": result})
        else:
            output.print_success(f"Created folder: {name}")
    except LlxError as e:
        raise output.fail(e)


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.0f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"
