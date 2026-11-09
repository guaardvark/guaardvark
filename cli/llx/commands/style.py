"""Style analysis: measure a video's shooting and cutting style, keep it as a recipe."""

from pathlib import Path

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError, get_client
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console

console = make_console()
style_app = typer.Typer(help="Measure a video's style; save and compare style recipes", no_args_is_help=True)

_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def _client(server):
    return get_client(server or get_global_server())


def _unwrap(data):
    return data.get("data", data) if isinstance(data, dict) else data


def _video_ref(api_client, video: str) -> dict:
    """A document id as given, a local file uploaded to the library, or a server-side path."""
    ref = (video or "").strip()
    if not ref:
        raise LlxError("video is required (document id, outputs URL, or path to a video file)")
    if ref.isdigit() or ref.startswith(("/api/", "guaardvark://")):
        return {"video": ref}
    path = Path(ref).expanduser()
    if path.is_file():
        if path.suffix.lower() not in _VIDEO_EXT:
            raise LlxError(f"video must be one of {', '.join(sorted(_VIDEO_EXT))}")
        data = _unwrap(api_client.upload("/api/files/upload", path, folder_path="", tags="video-style", auto_index="false"))
        doc_id = data.get("id")
        if not doc_id:
            raise LlxError("upload did not return a document id")
        return {"video_document_id": int(doc_id)}
    return {"path": ref}


def _run(fn):
    try:
        fn()
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


def _summary_rows(job: dict) -> list:
    p = job.get("profile") or {}
    c = p.get("cutting") or {}
    look = p.get("look") or {}
    return [
        {"field": "job", "value": job.get("id", "")},
        {"field": "status", "value": f"{job.get('status', '')} ({job.get('stage', '')}, {job.get('progress', 0)}%)"},
        {"field": "shots", "value": c.get("shot_count", "")},
        {"field": "average shot", "value": f"{c.get('asl_s', '')} s"},
        {"field": "transitions", "value": c.get("transition_mix", "")},
        {"field": "motion", "value": p.get("motion_mix", "")},
        {"field": "key", "value": look.get("key_mix", "")},
        {"field": "colour temp", "value": f"{look.get('cct_k_median', '')} K"},
        {"field": "cuts on beat", "value": (p.get("beat_sync") or {}).get("frac_cuts_on_beat", "")},
        {"field": "style", "value": job.get("style_clause", "")},
    ]


@style_app.command("analyze")
def style_analyze(
    video: str = typer.Argument(..., help="Document id, outputs URL, server path, or a local video file to upload"),
    name: str = typer.Option(None, "--name", "-n", help="Name for the analysis and a saved recipe"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Run inline and print the profile"),
    threshold: float = typer.Option(None, "--threshold", help="Shot detection sensitivity (ffmpeg scdet, default 8)"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Measure a video's shots, motion, look and pacing."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    def go():
        api = _client(server)
        body = _video_ref(api, video)
        if name:
            body["name"] = name
        if wait:
            body["sync"] = True
        if threshold is not None:
            body["scdet_threshold"] = threshold
        data = _unwrap(api.post("/api/video-style/analyze", json=body))
        if json_out or output.is_pipe():
            output.print_json(data)
            return
        if data.get("warning"):
            output.print_warning(data["warning"])
        output.print_table(_summary_rows(data), columns=["field", "value"], title="Video style")

    _run(go)


@style_app.command("list")
def style_list(server: str = typer.Option(None, "--server", "-s"), json_out: bool = typer.Option(False, "--json", "-j")):
    """Recent analyses."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    def go():
        rows = _unwrap(_client(server).get("/api/video-style")).get("jobs", [])
        if json_out or output.is_pipe():
            output.print_json(rows)
            return
        table = [{"id": r.get("id"), "name": r.get("name"), "status": r.get("status"), "stage": r.get("stage"),
                  "progress": r.get("progress"), "created": (r.get("created_at") or "")[:16]} for r in rows]
        output.print_table(table, columns=["id", "name", "status", "stage", "progress", "created"],
                           title=f"Video style analyses ({len(table)})")

    _run(go)


@style_app.command("status")
def style_status(job: str = typer.Argument(...), server: str = typer.Option(None, "--server", "-s"),
                    json_out: bool = typer.Option(False, "--json", "-j")):
    """One analysis: progress, and the profile once complete."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    def go():
        data = _unwrap(_client(server).get(f"/api/video-style/{job}"))
        if json_out or output.is_pipe():
            output.print_json(data)
            return
        output.print_table(_summary_rows(data), columns=["field", "value"], title=f"Video style {job}")

    _run(go)


@style_app.command("shots")
def style_shots(job: str = typer.Argument(...), server: str = typer.Option(None, "--server", "-s"),
                   json_out: bool = typer.Option(False, "--json", "-j")):
    """Per-shot measurements."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    def go():
        data = _unwrap(_client(server).get(f"/api/video-style/{job}/shots"))
        if json_out or output.is_pipe():
            output.print_json(data)
            return
        table = []
        for s in data.get("shots", []):
            look, motion, scale = s.get("look") or {}, s.get("motion") or {}, s.get("scale") or {}
            table.append({
                "#": s.get("i"), "in": s.get("t0"), "dur": s.get("dur_s"),
                "trans": (s.get("transition_in") or {}).get("type"),
                "motion": motion.get("label"), "scale": scale.get("label") or "-",
                "key": look.get("key"), "K": look.get("cct_k"), "sat": look.get("saturation"),
                "beat": (s.get("audio") or {}).get("cut_on_beat", ""),
            })
        output.print_table(table, columns=["#", "in", "dur", "trans", "motion", "scale", "key", "K", "sat", "beat"],
                           title=f"Shots ({len(table)})")

    _run(go)


@style_app.command("recipes")
def style_recipes(server: str = typer.Option(None, "--server", "-s"), json_out: bool = typer.Option(False, "--json", "-j")):
    """Style recipes on disk, measured and hand-written."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    def go():
        rows = _unwrap(_client(server).get("/api/video-style/recipes")).get("recipes", [])
        if json_out or output.is_pipe():
            output.print_json(rows)
            return
        table = [{"slug": r.get("slug"), "name": r.get("name"), "measured": "yes" if r.get("measured") else "",
                  "asl": r.get("target_asl_s") or "", "from": r.get("derived_from") or ""} for r in rows]
        output.print_table(table, columns=["slug", "name", "measured", "asl", "from"], title=f"Style recipes ({len(table)})")

    _run(go)


@style_app.command("save-recipe")
def style_save_recipe(job: str = typer.Argument(...), name: str = typer.Option(None, "--name", "-n"),
                         description: str = typer.Option(None, "--description", "-d"),
                         overwrite: bool = typer.Option(False, "--overwrite"),
                         server: str = typer.Option(None, "--server", "-s"), json_out: bool = typer.Option(False, "--json", "-j")):
    """Save a finished analysis as a style recipe."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    def go():
        body = {"overwrite": overwrite}
        if name:
            body["name"] = name
        if description:
            body["description"] = description
        data = _unwrap(_client(server).post(f"/api/video-style/{job}/recipe", json=body))
        if json_out or output.is_pipe():
            output.print_json(data)
            return
        output.print_success(f"Saved {data.get('saved')}")
        if data.get("style_clause"):
            console.print(data["style_clause"])

    _run(go)


@style_app.command("compare")
def style_compare(a: str = typer.Argument(..., help="Job id or measured recipe name"),
                     b: str = typer.Argument(..., help="Job id or measured recipe name"),
                     preset: str = typer.Option("all", "--preset", help="all | pacing-only | look-only"),
                     server: str = typer.Option(None, "--server", "-s"), json_out: bool = typer.Option(False, "--json", "-j")):
    """Distance between two measured styles (0 = identical, 1 = unrelated)."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    def go():
        data = _unwrap(_client(server).post("/api/video-style/compare", json={"a": a, "b": b, "preset": preset}))
        if json_out or output.is_pipe():
            output.print_json(data)
            return
        rows = [{"term": k, "distance": v if v is not None else "-", "weight": data.get("weights", {}).get(k, "")}
                for k, v in (data.get("terms") or {}).items()]
        output.print_table(rows, columns=["term", "distance", "weight"], title=f"Distance {data.get('total')} ({preset})")

    _run(go)
