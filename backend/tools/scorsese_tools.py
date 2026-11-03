"""Chat and MCP tool: measure how a video is shot and cut."""

from __future__ import annotations

import logging
from pathlib import Path

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.backend_http import is_mcp_caller, is_mcp_transport, run_tool_in_backend

logger = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mts", ".m2ts"}


class AnalyzeVideoStyleTool(BaseTool):
    """Measure a video's shots, camera motion, lighting, colour and pacing."""

    name = "analyze_video_style"
    read_only = True
    destructive = False
    description = (
        "Measure how a video is shot and cut: shot boundaries and average shot length, camera "
        "motion per shot, lighting key and colour temperature, palette, loudness, beats and how "
        "cuts sit on them. Runs on the CPU with ffmpeg and OpenCV; no model is involved. Use "
        "when the user asks to analyze a video's style, editing, pacing or look, or wants a "
        "style recipe made from a video. Returns a job id to poll with get_generation_status, "
        "or the finished profile when wait is true."
    )
    parameters = {
        "video": ToolParameter(
            name="video",
            type="string",
            description=(
                "The video: a library document id or /api/files/document/<id>/download link, an "
                "/api/outputs/<path> URL, a guaardvark://outputs/<path> resource URI, or a file path. "
                "Over MCP the file must be in Guaardvark's uploads folder or in an outputs folder MCP "
                "resources serve; in chat, anywhere in its uploads, outputs or install folder."
            ),
            required=True,
        ),
        "name": ToolParameter(
            name="name", type="string", required=False,
            description="A name for the analysis and for a recipe saved from it. Defaults to the filename.",
        ),
        "save_recipe": ToolParameter(
            name="save_recipe", type="bool", required=False, default=False,
            description="When true and wait is true, also save the measured profile as a style recipe named `name`.",
        ),
        "wait": ToolParameter(
            name="wait", type="bool", required=False, default=False,
            description="Run inline and return the profile (a few seconds per minute of video) instead of a job id.",
        ),
    }

    def execute(self, video: str, name: str | None = None, save_recipe: bool = False,
                wait: bool = False, **kwargs) -> ToolResult:
        if is_mcp_transport(self):
            arguments = {"video": video, "name": name, "save_recipe": save_recipe, "wait": wait}
            return run_tool_in_backend(self.name, {k: v for k, v in arguments.items() if v is not None})
        try:
            from backend.services.scorsese import analyze, jobs, recipe as recipes
            from backend.utils.media_inputs import accepted_forms, document_file, document_id_from_ref, resolve_media_ref

            mcp = is_mcp_caller(self)
            found = resolve_media_ref(
                video, mcp=mcp, label="video", document_path=document_file, within_install=True,
                accepted=accepted_forms(mcp=mcp, documents=True, within_install=True),
            )
            if not found.path:
                return ToolResult(success=False, error=found.error or "video not found")
            if Path(found.path).suffix.lower() not in VIDEO_EXT:
                return ToolResult(success=False, error=f"not a video file: {Path(found.path).name}")
            title = (name or "").strip() or Path(found.path).stem
            job = jobs.new_job(found.path, name=title, document_id=document_id_from_ref(video))
            if not wait:
                from backend.tasks.scorsese_tasks import dispatch

                try:
                    dispatch(job["id"])
                except Exception as e:  # noqa: BLE001
                    why = getattr(e, "why", None) or str(e)
                    return ToolResult(success=False, error=f"The analysis was not started: {why}",
                                      metadata={"job_id": job["id"]})
                return ToolResult(
                    success=True,
                    output=(f"Measuring '{title}'. Job {job['id']}; poll GET /api/scorsese/{job['id']} "
                            "or get_generation_status, then save a recipe with POST /api/scorsese/<job>/recipe."),
                    metadata={"job_id": job["id"], "status_url": f"/api/scorsese/{job['id']}"},
                )
            from backend.tasks.scorsese_tasks import run_stage

            stage: str | None = "probing"
            while stage:
                stage = run_stage(job["id"], stage)
            job = jobs.load_job(job["id"]) or job
            if job.get("status") != "complete":
                return ToolResult(success=False, error=job.get("error") or "analysis did not finish",
                                  metadata={"job_id": job["id"]})
            analysis = analyze.load_analysis(job["dir"]) or {}
            rec = recipes.build_recipe(analysis, title)
            clause = recipes.style_clause(rec)
            saved = None
            if save_recipe:
                saved = str(recipes.save_recipe(rec))
            profile = analysis.get("profile") or {}
            cutting = profile.get("cutting") or {}
            summary = (
                f"{cutting.get('shot_count')} shots, average {cutting.get('asl_s')} s, "
                f"motion {profile.get('motion_mix')}, look {profile.get('look', {}).get('key_mix')} at "
                f"{profile.get('look', {}).get('cct_k_median')} K. {clause}"
            )
            if saved:
                summary += f" Recipe saved: {saved}."
            return ToolResult(
                success=True, output=summary,
                metadata={"job_id": job["id"], "profile": profile, "style_clause": clause, "recipe_path": saved,
                          "status_url": f"/api/scorsese/{job['id']}"},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("analyze_video_style failed")
            return ToolResult(success=False, error=str(e))
