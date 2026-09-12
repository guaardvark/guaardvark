"""Image generation and vision analysis tools for the agent system."""

import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.backend_http import backend_base_url as _backend_base_url
from backend.utils.backend_http import http_json as _http_json
from backend.utils.backend_http import is_mcp_transport, run_tool_in_backend

logger = logging.getLogger(__name__)

_KONTEXT_MODEL_IDS = frozenset({
    "kontext", "flux-kontext", "flux-kontext-dev", "flux.kontext",
})


def _unwrap_nested_prompt_json(prompt: str) -> tuple[str, list[int]]:
    """If the LLM stuffed a whole JSON blob into ``prompt``, extract fields.

    Common failure: prompt='{"prompt":"…","subject_ids":[26]}' with no real kwargs.
    """
    text = (prompt or "").strip()
    sids: list[int] = []
    if not text or text[0] not in "{[":
        return text, sids
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text, sids
    if not isinstance(data, dict):
        return text, sids
    inner = data.get("prompt")
    if isinstance(inner, str) and inner.strip():
        text = inner.strip()
    raw_ids = data.get("subject_ids") or data.get("cast_subject_ids") or []
    if isinstance(raw_ids, (list, tuple)):
        for x in raw_ids:
            try:
                sids.append(int(x))
            except (TypeError, ValueError):
                pass
    elif raw_ids not in (None, ""):
        try:
            sids.append(int(raw_ids))
        except (TypeError, ValueError):
            pass
    return text, sids


def _normalize_subject_ids(raw) -> list[int]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            raw = parsed
        except (json.JSONDecodeError, TypeError):
            # "26" or "26,27"
            parts = re.split(r"[\s,]+", raw)
            out = []
            for p in parts:
                try:
                    out.append(int(p))
                except ValueError:
                    pass
            return out
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    out = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out


def _match_cast_subjects(candidates: list[str], subjects: list[dict]) -> list[int]:
    """Ids of subjects whose trigger word or name matches a prompt token, in prompt order."""
    by_trigger = {}
    by_name = {}
    for s in subjects:
        tw = (s.get("trigger_word") or "").strip().lower()
        nm = (s.get("name") or "").strip().lower()
        if tw:
            by_trigger[tw] = s["id"]
            by_trigger[tw.replace(" ", "_")] = s["id"]
        if nm:
            by_name[nm] = s["id"]
            by_name[nm.replace(" ", "_")] = s["id"]
    found: list[int] = []
    seen: set[int] = set()
    for c in candidates:
        key = c.strip().lower()
        sid = by_trigger.get(key) or by_name.get(key)
        if sid and sid not in seen:
            seen.add(sid)
            found.append(sid)
    return found


def _resolve_cast_from_prompt(prompt: str) -> list[int]:
    """Match [trigger], trigger_word, or cast name tokens in the prompt to trained Subjects."""
    text = prompt or ""
    if not text.strip():
        return []
    candidates: list[str] = []
    # Bracket form: [batman_2]
    candidates.extend(re.findall(r"\[([^\]]+)\]", text))
    # Bare tokens that look like triggers (word with underscore or known pattern)
    for m in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9]*(?:_[a-zA-Z0-9]+)+)\b", text):
        candidates.append(m.group(1))
    if not candidates:
        return []

    try:
        from flask import has_app_context
        from backend.models import Subject, db

        def _lookup() -> list[int]:
            rows = (
                Subject.query.filter(
                    Subject.kind == "character",
                    Subject.lora_path.isnot(None),
                    Subject.lora_path != "",
                ).all()
            )
            return _match_cast_subjects(
                candidates, [{"id": s.id, "trigger_word": s.trigger_word, "name": s.name} for s in rows],
            )

        if has_app_context():
            return _lookup()
        from backend.utils.backend_http import in_mcp_process, request_json
        if in_mcp_process():
            # The MCP server has no Flask app; the Cast Library route lists the subjects.
            subjects = (request_json("GET", "/api/cast-library").data or {}).get("subjects") or []
            return _match_cast_subjects(
                candidates, [s for s in subjects if s.get("kind") == "character" and s.get("lora_path")],
            )
        from backend.app import get_or_create_app
        app = get_or_create_app()
        with app.app_context():
            try:
                return _lookup()
            finally:
                db.session.remove()
    except Exception as e:
        logger.warning("cast resolve from prompt failed: %s", e)
        return []


def _chat_copy_still(image_path: str) -> tuple[str, str]:
    """Copy cast still into public generated_images; return (path, url)."""
    from backend.config import OUTPUT_DIR

    src = Path(image_path)
    out_dir = Path(OUTPUT_DIR) / "generated_images"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"gen_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{src.suffix or '.png'}"
    dest = out_dir / name
    shutil.copy2(str(src), str(dest))
    return str(dest), f"/api/outputs/generated_images/{name}"


class ImageGeneratorTool(BaseTool):
    """
    Generate images from text descriptions using the local image generation pipeline.
    Use this when the user asks you to create, generate, draw, or make an image.
    """

    name = "generate_image"
    read_only = False
    destructive = False
    description = (
        "Generate an image from a text prompt. Returns the URL of the generated image. "
        "Use when the user asks to create, generate, draw, or visualize an image. "
        "For a trained Cast character, ALWAYS pass subject_ids as a separate array of "
        "numeric Cast Library IDs (e.g. subject_ids=[26] for Batman 2). Do NOT bury "
        "subject_ids inside the prompt string. Putting [batman_2] only in the prompt "
        "without subject_ids will NOT load the LoRA."
    )
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            description=(
                "Scene/action description only (pose, lighting, setting). "
                "Do not embed JSON here. For cast characters put identity in subject_ids, "
                "not as the whole prompt body. If quoting on-image text, put EXACT words "
                'in double quotes — e.g. title "BATMAN".'
            ),
            required=True,
        ),
        "subject_ids": ToolParameter(
            name="subject_ids",
            type="list",
            description=(
                "Optional. Numeric Cast Library subject IDs with trained LoRAs to lock "
                "identity (e.g. [26]). Separate parameter — never nest this inside prompt. "
                "Loads LoRA + trigger + vision bible. Required for consistent characters."
            ),
            required=False,
            default=None,
        ),
        "style": ToolParameter(
            name="style",
            type="string",
            description="Image style: 'realistic', 'artistic', 'anime', 'photographic', 'digital-art'. Default: 'realistic'.",
            required=False,
            default="realistic",
        ),
        "width": ToolParameter(
            name="width",
            type="int",
            description="Image width in pixels. Default: 1024. Options: 512, 768, 1024.",
            required=False,
            default=1024,
        ),
        "height": ToolParameter(
            name="height",
            type="int",
            description="Image height in pixels. Default: 1024. Options: 512, 768, 1024.",
            required=False,
            default=1024,
        ),
        "model": ToolParameter(
            name="model",
            type="string",
            description=(
                "Model to use. Default 'auto' — recommended; the system auto-picks the best "
                "downloaded model for the prompt (usually Z-Image-Turbo or SDXL). "
                "With subject_ids, base is taken from the character's train family (Z-Image/SDXL/FLUX). "
                "Only override when the user names a specific model: 'krea2-turbo', 'zimage-turbo', "
                "'sd-xl', 'sdxl-turbo', 'realistic-vision', 'epic-realism'."
            ),
            required=False,
            default="auto",
        ),
        "wait_for_result": ToolParameter(
            name="wait_for_result",
            type="bool",
            description=(
                "True (the default in chat): render now and return the image inline. "
                "False: queue the render as an image batch and return its batch id at once; "
                "poll get_generation_status for the file. Use False from a remote agent or "
                "when the GPU may be busy."
            ),
            required=False,
            default=True,
        ),
    }

    STUDIO_URL = "/images"

    def __init__(self):
        super().__init__()

    def execute(self, prompt: str, style: str = "realistic",
                width: int = 1024, height: int = 1024,
                model: str = "auto", subject_ids=None, wait_for_result: bool = True,
                **kwargs) -> ToolResult:
        """Chat/CLI stills — cast LoRA path when subject_ids resolve; else stills_pipeline.

        With ``wait_for_result=False`` the prompt goes through the batch image
        generator instead (the same queue the Images page uses) and the call
        returns the batch id without touching the GPU."""
        wait_for_result = str(wait_for_result).lower() in ("1", "true", "yes")
        remote = self._context.get("transport") == "mcp"
        # Unwrap LLM mistakes: entire JSON stuffed into prompt=
        prompt, nested_ids = _unwrap_nested_prompt_json(prompt or "")
        # Explicit kwargs win, then nested JSON, then kwargs aliases
        sid_list = _normalize_subject_ids(
            subject_ids
            if subject_ids is not None
            else kwargs.get("subject_ids") or kwargs.get("cast_subject_ids")
        )
        if nested_ids:
            for i in nested_ids:
                if i not in sid_list:
                    sid_list.append(i)
        # Auto-resolve [batman_2] / trigger tokens if still empty
        if not sid_list:
            sid_list = _resolve_cast_from_prompt(prompt)

        # Dimension hallucination guard (LLM may invent odd sizes)
        STANDARD_SIZES = {256, 384, 512, 640, 768, 896, 1024, 1280, 1536}
        if width not in STANDARD_SIZES or height not in STANDARD_SIZES:
            dim_pattern = re.compile(rf'(?:^|\D){width}\s*[xX×]\s*{height}(?:\D|$)')
            if not dim_pattern.search(prompt or ""):
                logger.info(
                    "ImageGeneratorTool: LLM guessed %sx%s, resetting to 1024x1024",
                    width, height,
                )
                width, height = 1024, 1024

        enhance = kwargs.get("enhance")  # none | offline | director | auto
        director = bool(kwargs.get("director") or kwargs.get("director_mode"))
        negative = kwargs.get("negative_prompt") or kwargs.get("negative") or ""
        seed = kwargs.get("seed")
        if seed is not None:
            try:
                seed = int(seed)
            except (TypeError, ValueError):
                seed = None

        logger.info(
            "ImageGeneratorTool: %sx%s model=%s subject_ids=%s wait=%s prompt=%r",
            width, height, model, sid_list, wait_for_result, (prompt or "")[:100],
        )
        if remote or not wait_for_result:
            # Outside the backend process (an MCP server) the render must not
            # happen here: hand it to the backend over HTTP, and wait there if asked.
            return self._queue(
                prompt, style=style, width=width, height=height, model=model,
                subject_ids=sid_list, negative_prompt=negative,
                steps=kwargs.get("steps"),
                guidance=kwargs.get("guidance") or kwargs.get("guidance_scale"),
                via_http=remote, wait=wait_for_result and remote,
            )

        try:
            if sid_list:
                from backend.services.character_still_pipeline import render_character_still
                import tempfile, time as _time
                out = os.path.join(
                    tempfile.gettempdir(), f"chat_cast_{int(_time.time() * 1000)}.png"
                )
                still = render_character_still(
                    prompt,
                    subject_ids=sid_list,
                    include_bible=True,
                    source="chat",
                    width=width,
                    height=height,
                    steps=kwargs.get("steps"),
                    guidance=kwargs.get("guidance") or kwargs.get("guidance_scale"),
                    seed=seed,
                    negative_prompt=negative,
                    output_path=out,
                    style=style,
                    keep_pipeline=False,
                )
                results = [still]
                cast_used = True
            else:
                from backend.services.stills_pipeline import run_stills_pipeline

                results = run_stills_pipeline(
                    [prompt],
                    model=model,
                    width=width,
                    height=height,
                    steps=kwargs.get("steps"),
                    guidance=kwargs.get("guidance") or kwargs.get("guidance_scale"),
                    style=style,
                    negative_prompt=negative,
                    seed=seed,
                    source="chat",
                    enhance=enhance,
                    director=director,
                    keep_pipeline=False,
                    output="chat_copy",
                    restore_faces=bool(kwargs.get("restore_faces", False)),
                    hold_gpu=True,
                    replace_legacy_sd_markers=False,
                )
                cast_used = False
            still = results[0] if results else None
            if not still:
                return ToolResult(success=False, error="No result from stills pipeline")

            if still.success and still.image_path and (
                still.image_url or os.path.exists(still.image_path)
            ):
                image_url = still.image_url
                image_path = still.image_path
                # Cast path writes temp files — promote to public generated_images URL
                if cast_used and image_path and not image_url:
                    try:
                        image_path, image_url = _chat_copy_still(image_path)
                    except Exception as e:
                        logger.warning("chat cast copy failed: %s", e)
                        image_url = image_path
                image_url = image_url or image_path
                filename = os.path.basename(image_path) if image_path else ""
                meta = still.metadata or {}
                cast_line = ""
                if cast_used:
                    cast_line = (
                        f"\nCast LoRA: ON subject_ids={sid_list} "
                        f"family={meta.get('family')} strength={meta.get('lora_strength')} "
                        f"lock={meta.get('lock_prefix')!r}"
                    )
                else:
                    cast_line = (
                        "\nCast LoRA: OFF (no subject_ids — base model only; "
                        "pass subject_ids=[id] for trained cast characters)"
                    )
                return ToolResult(
                    success=True,
                    output=(
                        f"Image generated successfully in {still.generation_time:.1f}s.\n"
                        f"Image URL: {image_url}\n"
                        f"Prompt used: {still.prompt_used}\n"
                        f"Style: {style}\n"
                        f"Size: {still.width}x{still.height}\n"
                        f"Steps/CFG: {still.steps}/{still.guidance}\n"
                        f"Enhance: {still.enhance_mode}\n"
                        f"Model: {still.model_used or model}\n"
                        f"Seed: {still.seed_used}"
                        f"{cast_line}"
                    ),
                    metadata={
                        "image_url": image_url,
                        "filename": filename,
                        "prompt": still.prompt_used,
                        "prompt_used": still.prompt_used,
                        "negative_used": still.negative_used,
                        "width": still.width,
                        "height": still.height,
                        "steps": still.steps,
                        "guidance": still.guidance,
                        "enhance_mode": still.enhance_mode,
                        "model": still.model_used or model,
                        "seed": still.seed_used,
                        "generation_time": still.generation_time,
                        "cast_used": cast_used,
                        "subject_ids": sid_list,
                        "lock_prefix": meta.get("lock_prefix"),
                        "lora_strength": meta.get("lora_strength"),
                        "family": meta.get("family"),
                    },
                )

            err = (still.error if still else None) or "Image generation failed."
            low = err.lower()
            if "out of memory" in low or "cuda" in low:
                err = (
                    "The GPU ran out of memory generating this image. "
                    "Try a smaller size, a lighter model, or wait for other "
                    "renders to finish, then try again."
                )
            return ToolResult(success=False, error=err)

        except ImportError:
            return ToolResult(
                success=False,
                error="Image generation pipeline not available. Diffusion models may not be installed.",
            )
        except Exception as e:
            logger.error(f"ImageGeneratorTool error: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Image generation failed: {e}")


    MAX_WAIT_S = 20 * 60
    POLL_INTERVAL_S = 3.0

    def _queue(self, prompt: str, *, style: str, width: int, height: int, model: str,
               subject_ids: list, negative_prompt: str, steps=None, guidance=None,
               via_http: bool = False, wait: bool = False) -> ToolResult:
        """Enqueue one prompt on the batch image generator: in-process inside the
        backend, over HTTP from anywhere else. Returns at once unless ``wait``."""
        params = {
            "model": model or "auto",
            "style": style,
            "width": width,
            "height": height,
            "negative_prompt": negative_prompt or "",
            "ui_config": {"source": "chat", "tool": self.name},
        }
        if steps is not None:
            params["steps"] = steps
        if guidance is not None:
            params["guidance"] = guidance
        if subject_ids:
            params["subject_ids"] = list(subject_ids)
        try:
            if via_http:
                data = _http_json("POST", "/api/batch-image/generate/prompts",
                                  {"prompts": [prompt], **params})
                batch_id = data["batch_id"]
            else:
                from backend.services.batch_image_generator import start_batch_from_prompts
                batch_id = start_batch_from_prompts([prompt], **params)
        except ImportError:
            return ToolResult(success=False, error="Batch image generation is not available.")
        except Exception as e:
            logger.error("ImageGeneratorTool queue failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"Could not queue the image: {e}")
        if wait:
            return self._wait_http(batch_id, prompt)
        cast_line = (
            f"Cast LoRA: ON subject_ids={list(subject_ids)}" if subject_ids
            else "Cast LoRA: OFF (pass subject_ids=[id] for trained cast characters)"
        )
        return ToolResult(
            success=True,
            output="\n".join([
                f"Image queued as batch {batch_id}.",
                f"Prompt: {prompt}",
                f"Size: {width}x{height} | Model: {model or 'auto'} | Style: {style}",
                cast_line,
                f"Poll: get_generation_status(batch_id=\"{batch_id}\")",
                f"Open Images: {self.STUDIO_URL}",
            ]),
            metadata={
                "prompt": prompt,
                "batch_id": batch_id,
                "queued": True,
                "studio_url": self.STUDIO_URL,
                "status_tool": "get_generation_status",
                "width": width,
                "height": height,
                "model": model or "auto",
                "subject_ids": list(subject_ids or []),
            },
        )


    def _wait_http(self, batch_id: str, prompt: str) -> ToolResult:
        """Poll the backend for a queued batch and return the finished file."""
        import time as _time
        deadline = _time.monotonic() + self.MAX_WAIT_S
        status_tool = GenerationStatusTool()
        status_tool.set_context(dict(self._context))
        while _time.monotonic() < deadline:
            _time.sleep(self.POLL_INTERVAL_S)
            result = status_tool.execute(batch_id=batch_id)
            if not result.success:
                return result
            info = result.metadata or {}
            if info.get("status") == "completed" and info.get("files"):
                f = info["files"][0]
                return ToolResult(
                    success=True,
                    output="\n".join([
                        "Image generated successfully"
                        + (f" in {f['generation_time']:.1f}s." if f.get("generation_time") else "."),
                        f"Image URL: {f['url']}",
                        f"Prompt used: {prompt}",
                        f"Model: {f.get('model') or 'auto'}",
                        f"Batch: {batch_id}",
                    ]),
                    metadata={"image_url": f["url"], "batch_id": batch_id, "prompt": prompt,
                              "model": f.get("model"), "generation_time": f.get("generation_time")},
                )
            if info.get("status") in ("error", "cancelled") or (
                info.get("status") == "completed" and not info.get("files")
            ):
                err = info.get("error") or "; ".join(info.get("errors") or []) or info.get("status")
                return ToolResult(success=False, error=f"Image generation failed: {err}")
        return ToolResult(
            success=True,
            output=f"Image still rendering after {self.MAX_WAIT_S // 60} minutes (batch {batch_id}). "
                   f"Poll get_generation_status(batch_id=\"{batch_id}\"); it is not a failure.",
            metadata={"batch_id": batch_id, "queued": True, "still_running": True, "prompt": prompt},
        )


class GenerationStatusTool(BaseTool):
    """Read the state of a queued image or video batch by id."""

    name = "get_generation_status"
    read_only = True
    idempotent = True
    description = (
        "Report the state of a queued generation: an image batch (ImageBatch_...) from "
        "generate_image with wait_for_result=false or the batch image route, or a video batch "
        "from generate_video. Returns status, progress, and the URL of each finished file. "
        "Use after a queued generate call, or when the user asks whether a render is done."
    )
    parameters = {
        "batch_id": ToolParameter(
            name="batch_id",
            type="string",
            description="The batch id a generate tool returned (e.g. ImageBatch_09-11-2026_132620_013).",
            required=True,
        ),
    }

    def __init__(self):
        super().__init__()

    @staticmethod
    def _image_status(batch_id: str):
        from backend.services.batch_image_generator import get_batch_image_generator
        generator = get_batch_image_generator()
        status = generator.find_batch_status(batch_id, include_results=True)
        if status is None:
            return None
        files = []
        for r in status.results or []:
            if r.success and r.image_path:
                name = os.path.basename(r.image_path)
                files.append({
                    "url": f"/api/batch-image/image/{batch_id}/{name}",
                    "path": r.image_path,
                    "model": (r.metadata or {}).get("model_used"),
                    "generation_time": r.generation_time,
                })
        failed = [r.error for r in (status.results or []) if not r.success and r.error]
        return {
            "kind": "image",
            "batch_id": batch_id,
            "status": status.status,
            "completed": status.completed_images,
            "failed": status.failed_images,
            "total": status.total_images,
            "error": status.error,
            "errors": failed,
            "files": files,
            "studio_url": ImageGeneratorTool.STUDIO_URL,
        }

    @staticmethod
    def _image_status_http(batch_id: str):
        try:
            d = _http_json("GET", f"/api/batch-image/status/{batch_id}?include_results=true")
        except RuntimeError as e:
            if "not found" in str(e).lower() or "404" in str(e):
                return None
            raise
        if not d or not d.get("status"):
            return None
        files = []
        for r in d.get("results") or []:
            if r.get("success") and r.get("image_path"):
                name = os.path.basename(r["image_path"])
                files.append({
                    "url": f"/api/batch-image/image/{batch_id}/{name}",
                    "path": r["image_path"],
                    "model": (r.get("metadata") or {}).get("model_used"),
                    "generation_time": r.get("generation_time"),
                })
        failed = [r.get("error") for r in (d.get("results") or []) if not r.get("success") and r.get("error")]
        return {
            "kind": "image", "batch_id": batch_id, "status": d.get("status"),
            "completed": d.get("completed_images"), "failed": d.get("failed_images"),
            "total": d.get("total_images"), "error": d.get("error"), "errors": failed,
            "files": files, "studio_url": ImageGeneratorTool.STUDIO_URL,
        }

    @staticmethod
    def _video_status_http(batch_id: str):
        try:
            d = _http_json("GET", f"/api/batch-video/status/{batch_id}")
        except RuntimeError as e:
            if "not found" in str(e).lower() or "404" in str(e):
                return None
            raise
        if not d or not d.get("status"):
            return None
        files = []
        for r in d.get("results") or []:
            if r.get("success") and r.get("video_path"):
                entry = {"url": f"/api/batch-video/video/{batch_id}/{r['video_path']}"}
                if r.get("thumbnail_path"):
                    entry["thumbnail_url"] = f"/api/batch-video/video/{batch_id}/{r['thumbnail_path']}"
                files.append(entry)
        failed = [r.get("error") for r in (d.get("results") or []) if not r.get("success") and r.get("error")]
        return {
            "kind": "video", "batch_id": batch_id, "status": d.get("status"), "stage": d.get("stage"),
            "completed": d.get("completed_videos"), "failed": len(failed), "total": d.get("total_videos"),
            "error": d.get("error"), "errors": failed, "files": files,
            "studio_url": f"/video?batch={batch_id}",
        }

    @staticmethod
    def _video_status(batch_id: str):
        from backend.services.batch_video_generator import get_batch_video_generator
        generator = get_batch_video_generator()
        status = generator.get_batch_status(batch_id)
        if status is None:
            return None
        files = []
        for r in status.results or []:
            if r.success and r.video_path:
                entry = {"url": f"/api/batch-video/video/{batch_id}/{r.video_path}"}
                if r.thumbnail_path:
                    entry["thumbnail_url"] = f"/api/batch-video/video/{batch_id}/{r.thumbnail_path}"
                files.append(entry)
        failed = [r.error for r in (status.results or []) if not r.success and r.error]
        completed = sum(1 for r in (status.results or []) if r.success)
        return {
            "kind": "video",
            "batch_id": batch_id,
            "status": status.status,
            "stage": getattr(status, "stage", None),
            "completed": completed,
            "failed": len(failed),
            "total": getattr(status, "total_videos", None),
            "error": getattr(status, "error", None),
            "errors": failed,
            "files": files,
            "studio_url": f"/video?batch={batch_id}",
        }

    def execute(self, batch_id: str, **kwargs) -> ToolResult:
        batch_id = (batch_id or "").strip()
        if not batch_id:
            return ToolResult(success=False, error="batch_id is required")
        info = None
        remote = self._context.get("transport") == "mcp"
        readers = (self._image_status_http, self._video_status_http) if remote else (
            self._image_status, self._video_status)
        for reader in readers:
            try:
                info = reader(batch_id)
            except ImportError:
                continue
            except Exception as e:
                logger.warning("get_generation_status %s via %s: %s", batch_id, reader.__name__, e)
                continue
            if info is not None:
                break
        if info is None:
            return ToolResult(success=False, error=f"No image or video batch named {batch_id}")
        total = info.get("total")
        done = info.get("completed") or 0
        head = f"{info['kind'].title()} batch {batch_id}: {info['status']}"
        if total:
            head += f" ({done}/{total} finished"
            if info.get("failed"):
                head += f", {info['failed']} failed"
            head += ")"
        lines = [head]
        for f in info["files"]:
            lines.append(f"File: {f['url']}")
        if info.get("error"):
            lines.append(f"Error: {info['error']}")
        for err in info.get("errors") or []:
            lines.append(f"Failed item: {err}")
        if info["status"] in ("queued", "pending", "running"):
            lines.append("Still running; poll again in a few seconds.")
        lines.append(f"Open Studio: {info['studio_url']}")
        return ToolResult(success=True, output="\n".join(lines), metadata=info)


class AnimationGeneratorTool(BaseTool):
    """
    Generate an animated GIF/video from a text description with motion.
    Use this when the user asks to animate a still into a short looping GIF
    or frame-morph clip. For a real video clip (Wan, MiniMax, LTX, …) use
    generate_video instead.
    """

    name = "generate_animation"
    read_only = False
    destructive = False
    description = (
        "Generate a short looping GIF or frame-morph MP4 from a text prompt with "
        "motion description, via Stable Diffusion img2img. Use when the user asks "
        "to animate, create a GIF, or make a looping frame morph. For a cinema "
        "clip from a video model use generate_video instead."
    )
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            description="Detailed description of the scene to animate.",
            required=True,
        ),
        "motion": ToolParameter(
            name="motion",
            type="string",
            description="What moves or changes between frames (e.g. 'walking forward', 'waving hand', 'clouds drifting').",
            required=True,
        ),
        "frames": ToolParameter(
            name="frames",
            type="int",
            description="Number of frames to generate (2-24). Default: 8. More frames = smoother but slower.",
            required=False,
            default=8,
        ),
        "strength": ToolParameter(
            name="strength",
            type="float",
            description="How much each frame changes from the previous (0.1=subtle, 0.3=moderate, 0.5=dramatic). Default: 0.20.",
            required=False,
            default=0.20,
        ),
        "format": ToolParameter(
            name="format",
            type="string",
            description="Output format: 'gif', 'mp4', or 'both'. Default: 'both'.",
            required=False,
            default="both",
        ),
        "vision_steering": ToolParameter(
            name="vision_steering",
            type="bool",
            description="Use vision model to guide frame evolution (slower but more coherent). Default: false.",
            required=False,
            default=False,
        ),
    }

    def __init__(self):
        super().__init__()

    def execute(self, prompt: str, motion: str, frames: int = 8,
                strength: float = 0.20, format: str = "both",
                vision_steering: bool = False, **kwargs) -> ToolResult:
        logger.info(f"AnimationGeneratorTool: prompt={prompt[:60]}..., motion={motion}, frames={frames}")

        if is_mcp_transport(self):
            # The frames render on the GPU, which belongs to the backend process.
            return run_tool_in_backend(self.name, {
                "prompt": prompt, "motion": motion, "frames": frames, "strength": strength,
                "format": format, "vision_steering": vision_steering,
            }, read_timeout=31 * 60)

        try:
            from backend.services.animation_generator import (
                get_animation_generator, AnimationRequest
            )

            anim_gen = get_animation_generator()

            request = AnimationRequest(
                prompt=prompt,
                motion_prompt=motion,
                num_frames=frames,
                strength=strength,
                output_format=format,
                use_vision_steering=vision_steering,
            )

            from backend.services.gpu_resource_policy import gpu_session
            from backend.services.job_operation_gate import GpuBusyError
            from backend.services.job_types import JobKind
            from backend.services.offline_image_generator import get_image_generator
            try:
                # Use the image gen's ram estimate for the animation (reuses SD pipeline)
                img_gen = get_image_generator()
                ram_est = img_gen._ram_estimate_gb(request.model) if hasattr(img_gen, "_ram_estimate_gb") else 6.0
                with gpu_session(JobKind.VIDEO_RENDER, f"chat_anim_{uuid.uuid4().hex[:8]}",
                                 on_busy="raise", evict_ollama=True, vram_estimate_mb=8000,
                                 ram_estimate_gb=ram_est, require_fit=True, cross_process=True):
                    result = anim_gen.generate(request)
            except GpuBusyError:
                return ToolResult(
                    success=False,
                    error="GPU is busy with another render right now — try again in a moment.",
                )

            if result.success:
                output_lines = [
                    f"Animation generated successfully in {result.generation_time:.1f}s.",
                    f"Frames: {result.frame_count} | FPS: request.fps",
                    f"Prompt: {prompt}",
                    f"Motion: {motion}",
                ]
                metadata = {
                    "prompt": prompt,
                    "motion": motion,
                    "frame_count": result.frame_count,
                    "generation_time": result.generation_time,
                }

                if result.gif_url:
                    output_lines.append(f"GIF: {result.gif_url}")
                    metadata["gif_url"] = result.gif_url
                    metadata["image_url"] = result.gif_url  # For inline display
                if result.mp4_url:
                    output_lines.append(f"MP4: {result.mp4_url}")
                    metadata["video_url"] = result.mp4_url

                return ToolResult(
                    success=True,
                    output="\n".join(output_lines),
                    metadata=metadata,
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.error or "Animation generation failed",
                )

        except ImportError:
            return ToolResult(
                success=False,
                error="Animation generation dependencies not available.",
            )
        except Exception as e:
            logger.error(f"AnimationGeneratorTool error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Animation generation failed: {str(e)}",
            )


def _dims_for_ratio(ratio: str, caps: dict) -> tuple:
    """A width/height for a ratio inside the model's pixel budget, aligned to
    its grid; mirrors the page's fitAreaToRatio so the tool and the UI agree."""
    try:
        w_part, h_part = ratio.split(":")
        r = float(w_part) / float(h_part)
    except (ValueError, ZeroDivisionError):
        return None, None
    align = int(caps.get("dimension_alignment") or 16)
    declared = int(caps.get("max_pixel_area") or 0)
    # The usual 16 GB working budget, never above the declared cap.
    budget = min(declared, 864 * 480) if declared else 832 * 480
    width = (budget * r) ** 0.5
    height = width / r
    width = max(align, int(round(width / align) * align))
    height = max(align, int(round(height / align) * align))
    return width, height


def _media_path(ref: Optional[str]) -> Optional[str]:
    """A local path for a Document id or a path the caller already has."""
    if ref is None:
        return None
    text = str(ref).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            from backend.models import Document
            from backend.services.document_path_resolver import resolve_document_path
            doc = Document.query.get(int(text))
            path = resolve_document_path(doc) if doc else None
            return str(path) if path else None
        except Exception as e:  # noqa: BLE001 — a missing row is a plain "not found"
            logger.info("document %s did not resolve: %s", text, e)
            return None
    return text if os.path.exists(text) else None


class VideoGeneratorTool(BaseTool):
    """Generate a real video clip via the batch video pipeline.

    Use when the user asks to create/generate/make a video from a description.
    (Contrast generate_animation, which produces short frame-morph GIF loops.)
    Every knob is checked against the capability record the model declares in
    the video model registry, so an ask the model cannot honour (sound from a
    silent family, references on a build without them, a length past its
    longest clip) fails with one sentence instead of a wrong clip."""

    name = "generate_video"
    read_only = False
    destructive = False
    description = (
        "Queue a video clip from a text prompt using a local video model. Returns "
        "immediately with a batch id and Studio deep-link so long jobs are not "
        "false-timeouted. Use when the user asks to create, generate, or make a "
        "video. Pick model='minimax-h3-int8' (or audio=true) for a clip with its "
        "own soundtrack and spoken dialogue; give first_image / last_image to "
        "animate between frames; reference_images and reference_audio lock a "
        "person, look or voice on the reference build. For short looping "
        "frame-morph animations use generate_animation instead."
    )
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            description="Description of the video to generate (scene, subject, motion, style, any spoken lines).",
            required=True,
        ),
        "model": ToolParameter(
            name="model",
            type="string",
            description="Video model id from the registry (e.g. wan22-5b, minimax-h3-int8). Default: the installed default.",
            required=False,
        ),
        "aspect_ratio": ToolParameter(
            name="aspect_ratio",
            type="string",
            description="16:9, 9:16, 1:1, 4:3, 3:2, 21:9 or 3:4; must be one the model declares.",
            required=False,
        ),
        "duration_s": ToolParameter(
            name="duration_s",
            type="float",
            description="Clip length in seconds; clamped to the model's declared range. Overrides duration_frames.",
            required=False,
        ),
        "duration_frames": ToolParameter(
            name="duration_frames",
            type="int",
            description="Number of frames (legacy). Default 49. Clamped to the model's longest clip.",
            required=False,
            default=49,
        ),
        "num_inference_steps": ToolParameter(
            name="num_inference_steps",
            type="int",
            description="Inference steps. Omit to use the model's default; a value below the model's floor is raised to it.",
            required=False,
        ),
        "audio": ToolParameter(
            name="audio",
            type="boolean",
            description="Require a model that generates its own soundtrack (MiniMax H3). Fails on a silent family.",
            required=False,
            default=False,
        ),
        "first_image": ToolParameter(
            name="first_image",
            type="string",
            description="Document id or path of the first frame (image-to-video).",
            required=False,
        ),
        "last_image": ToolParameter(
            name="last_image",
            type="string",
            description="Document id or path of the last frame; needs a model with first+last-frame mode.",
            required=False,
        ),
        "reference_images": ToolParameter(
            name="reference_images",
            type="list",
            description="Document ids or paths of reference images (identity, look); needs the reference build.",
            required=False,
        ),
        "reference_audio": ToolParameter(
            name="reference_audio",
            type="string",
            description="Document id or path of a voice or music reference; needs the reference build.",
            required=False,
        ),
        "speed_profile": ToolParameter(
            name="speed_profile",
            type="string",
            description="A speed profile the model declares (e.g. turbo-8).",
            required=False,
        ),
        "style": ToolParameter(
            name="style",
            type="string",
            description="Prompt style: cinematic, realistic, artistic, anime, 3d_animation, stop_motion, hand_drawn, western_cartoon, none.",
            required=False,
        ),
        "wait_for_result": ToolParameter(
            name="wait_for_result",
            type="boolean",
            description=(
                "If true, poll until the clip finishes (or timeout). Default false: "
                "enqueue and return a Studio Video Gen / Jobs link immediately."
            ),
            required=False,
            default=False,
        ),
    }

    # Optional short preview wait only — cinema/Wan can exceed 30+ minutes.
    POLL_INTERVAL_S = 3
    MAX_WAIT_S = 1800

    def __init__(self):
        super().__init__()

    @staticmethod
    def resolve_request(prompt: str, *, model: Optional[str] = None, aspect_ratio: Optional[str] = None,
                        duration_s: Optional[float] = None, duration_frames: Optional[int] = None,
                        num_inference_steps: Optional[int] = None, audio: bool = False,
                        first_image: Optional[str] = None, last_image: Optional[str] = None,
                        reference_images: Optional[list] = None, reference_audio: Optional[str] = None,
                        speed_profile: Optional[str] = None, style: Optional[str] = None) -> tuple:
        """Turn the tool's arguments into batch parameters checked against the
        model's capability record. Returns (params, None) or (None, message).
        Pure: no service is touched, so the rules are testable."""
        from backend.services.video_model_registry import (
            DEFAULT_T2V_MODEL, VIDEO_MODEL_REGISTRY, model_capabilities, i2v_model_for,
            resolve_active_video_model,
        )
        model_id = (model or "").strip()
        if not model_id:
            role = "i2v" if first_image else "t2v"
            picked, _resolve_err = resolve_active_video_model(role)
            model_id = picked or DEFAULT_T2V_MODEL
        entry = VIDEO_MODEL_REGISTRY.get(model_id)
        if not entry:
            known = ", ".join(k for k in VIDEO_MODEL_REGISTRY if model_capabilities(k))
            return None, f"Unknown video model '{model_id}'. Known: {known}."
        caps = model_capabilities(model_id)
        if not caps:
            return None, f"'{model_id}' is a companion file, not a video model."
        if audio and not caps.get("audio_out"):
            return None, (
                f"{entry['name']} renders silent clips. For a clip with its own soundtrack use "
                f"a model that declares audio, such as minimax-h3-int8."
            )
        refs = [str(r) for r in (reference_images or []) if str(r).strip()]
        if (refs or reference_audio) and "ref2v" not in caps["modes"]:
            return None, (
                f"{entry['name']} takes no reference images or audio; use the reference build "
                f"(minimax-h3-ref2va-int8) for identity, look or voice references."
            )
        if last_image and "flf2v" not in caps["modes"] and "l2v" not in caps["modes"]:
            return None, f"{entry['name']} has no last-frame mode; drop last_image or pick minimax-h3-int8."
        if first_image and not caps.get("supports_i2v") and "ref2v" not in caps["modes"]:
            sibling = i2v_model_for(model_id)
            if sibling != model_id and sibling in VIDEO_MODEL_REGISTRY:
                model_id, entry, caps = sibling, VIDEO_MODEL_REGISTRY[sibling], model_capabilities(sibling)
            else:
                return None, f"{entry['name']} cannot animate a first frame."

        fps = int(caps.get("native_fps") or 24)
        max_frames = int(caps.get("max_frames") or 121)
        if duration_s is not None:
            try:
                frames = int(round(float(duration_s) * fps))
            except (TypeError, ValueError):
                return None, f"duration_s must be a number, got {duration_s!r}"
        else:
            try:
                frames = int(duration_frames or 49)
            except (TypeError, ValueError):
                frames = 49
        min_clip = caps.get("min_clip_s")
        frames = max(int(round((min_clip or 0) * fps)) or 9, min(frames, max_frames))

        steps = None
        if num_inference_steps not in (None, ""):
            try:
                steps = max(1, min(int(num_inference_steps), 100))
            except (TypeError, ValueError):
                return None, f"num_inference_steps must be a number, got {num_inference_steps!r}"
            floor = int(caps.get("min_steps") or 0)
            if floor and steps < floor:
                steps = floor
        if speed_profile and speed_profile not in (caps.get("speed_profiles") or {}):
            declared = ", ".join(caps.get("speed_profiles") or {}) or "none"
            return None, f"{entry['name']} declares no speed profile '{speed_profile}' (declared: {declared})."

        ratios = caps.get("aspect_ratios") or []
        ratio = (aspect_ratio or "").strip()
        if ratio and ratios and ratio not in ratios:
            return None, f"{entry['name']} renders {', '.join(ratios)}; {ratio} is not one of them."
        params = {
            "model": model_id,
            "duration_frames": frames,
            "fps": fps,
            "metadata": {"source": "chat"},
        }
        if ratio:
            params["metadata"]["aspect_ratio"] = ratio
            w, h = _dims_for_ratio(ratio, caps)
            if w and h:
                params["width"], params["height"] = w, h
        if steps is not None:
            params["num_inference_steps"] = steps
            params["metadata"]["steps_explicit"] = True
        elif caps.get("default_steps"):
            params["num_inference_steps"] = int(caps["default_steps"])
        if speed_profile:
            params["speed_profile"] = speed_profile
        if style:
            params["prompt_style"] = style
            if style == "none":
                params["enhance_prompt"] = False
        return params, None

    def execute(self, prompt: str, duration_frames: int = 49,
                num_inference_steps=None, wait_for_result: bool = False,
                model: Optional[str] = None, aspect_ratio: Optional[str] = None,
                duration_s=None, audio: bool = False, first_image: Optional[str] = None,
                last_image: Optional[str] = None, reference_images=None,
                reference_audio: Optional[str] = None, speed_profile: Optional[str] = None,
                style: Optional[str] = None, **kwargs) -> ToolResult:
        import time as _time

        prompt = (prompt or "").strip()
        if not prompt:
            return ToolResult(success=False, error="Empty video prompt")

        if is_mcp_transport(self):
            # Model preflight, document lookups and the batch queue belong to the backend process.
            arguments = {
                "prompt": prompt, "duration_frames": duration_frames,
                "num_inference_steps": num_inference_steps, "wait_for_result": wait_for_result,
                "model": model, "aspect_ratio": aspect_ratio, "duration_s": duration_s, "audio": audio,
                "first_image": first_image, "last_image": last_image, "reference_images": reference_images,
                "reference_audio": reference_audio, "speed_profile": speed_profile, "style": style,
            }
            return run_tool_in_backend(
                self.name, {k: v for k, v in arguments.items() if v is not None},
                read_timeout=self.MAX_WAIT_S + 60,
            )
        wait_for_result = str(wait_for_result).lower() in ("1", "true", "yes")
        audio = str(audio).lower() in ("1", "true", "yes")
        if isinstance(reference_images, str):
            reference_images = [x.strip() for x in reference_images.split(",") if x.strip()]

        params, err = self.resolve_request(
            prompt, model=model, aspect_ratio=aspect_ratio, duration_s=duration_s,
            duration_frames=duration_frames, num_inference_steps=num_inference_steps, audio=audio,
            first_image=first_image, last_image=last_image, reference_images=reference_images,
            reference_audio=reference_audio, speed_profile=speed_profile, style=style,
        )
        if err:
            return ToolResult(success=False, error=err)
        model_id = params["model"]
        duration_frames = params["duration_frames"]
        num_inference_steps = params.get("num_inference_steps")

        logger.info("VideoGeneratorTool: model=%s frames=%s steps=%s wait=%s prompt=%r",
                    model_id, duration_frames, num_inference_steps, wait_for_result, prompt[:100])
        try:
            from backend.services.batch_video_generator import get_batch_video_generator
            from backend.services.video_model_registry import preflight_video_model

            ready, preflight_err = preflight_video_model(model_id)
            if not ready:
                return ToolResult(success=False, error=f"Video model not ready: {preflight_err}")

            generator = get_batch_video_generator()
            if not generator.service_available:
                return ToolResult(success=False, error="Video generation service not available")

            first_path = _media_path(first_image) if first_image else None
            if first_image and not first_path:
                return ToolResult(success=False, error=f"first_image not found: {first_image}")
            last_path = _media_path(last_image) if last_image else None
            if last_image and not last_path:
                return ToolResult(success=False, error=f"last_image not found: {last_image}")
            ref_paths = [_media_path(r) for r in (reference_images or [])]
            if any(p is None for p in ref_paths):
                return ToolResult(success=False, error="A reference image was not found")
            ref_audio_path = _media_path(reference_audio) if reference_audio else None
            if reference_audio and not ref_audio_path:
                return ToolResult(success=False, error=f"reference_audio not found: {reference_audio}")

            if ref_paths or ref_audio_path:
                # The reference build reads the references from the batch
                # metadata; the prompt names them as <Picture N> / <Audio N>
                # in wiring order.
                params["metadata"].update({
                    "ref_images": ref_paths,
                    "ref_audios": [ref_audio_path] if ref_audio_path else [],
                })
                status = generator.start_batch_from_prompts(prompts=[prompt], **params)
            elif first_path:
                status = generator.start_batch_from_images(
                    image_paths=[first_path], prompt=prompt,
                    last_frame_paths=[last_path] if last_path else [], **params,
                )
            else:
                status = generator.start_batch_from_prompts(prompts=[prompt], **params)
            batch_id = status.batch_id
            studio_url = f"/video?batch={batch_id}"
            jobs_url = "/tasks"

            if not wait_for_result:
                stage = getattr(status, "stage", "queued") or "queued"
                return ToolResult(
                    success=True,
                    output="\n".join([
                        f"Video generation queued as batch {batch_id} (stage: {stage}).",
                        f"Prompt: {prompt}",
                        f"Frames: {duration_frames} | Steps: {num_inference_steps}",
                        f"Open Video Gen: {studio_url}",
                        f"Jobs: {jobs_url}",
                        "The clip will appear in Studio → Video Gen when finished.",
                    ]),
                    metadata={
                        "prompt": prompt,
                        "batch_id": batch_id,
                        "queued": True,
                        "stage": stage,
                        "studio_url": studio_url,
                        "jobs_url": jobs_url,
                    },
                )

            deadline = _time.monotonic() + self.MAX_WAIT_S
            while _time.monotonic() < deadline:
                _time.sleep(self.POLL_INTERVAL_S)
                status = generator.get_batch_status(batch_id)
                if status is None:
                    return ToolResult(success=False, error=f"Video batch {batch_id} disappeared")
                stage = getattr(status, "stage", None)
                if stage:
                    logger.info("VideoGeneratorTool batch %s stage=%s", batch_id, stage)
                if status.status == "completed":
                    break
                if status.status in ("error", "cancelled"):
                    err = status.error or (
                        status.results[0].error if status.results and status.results[0].error
                        else status.status
                    )
                    return ToolResult(success=False, error=f"Video generation failed: {err}")
            else:
                return ToolResult(
                    success=True,
                    output="\n".join([
                        f"Video generation still running after {self.MAX_WAIT_S // 60} minutes "
                        f"(batch {batch_id}).",
                        f"Open Video Gen: {studio_url}",
                        "It will finish in the background — this is not a failure.",
                    ]),
                    metadata={
                        "prompt": prompt,
                        "batch_id": batch_id,
                        "queued": True,
                        "still_running": True,
                        "studio_url": studio_url,
                    },
                )

            result = next((r for r in status.results if r.success and r.video_path), None)
            if not result:
                err = status.results[0].error if status.results else "no results"
                return ToolResult(success=False, error=f"Video generation failed: {err}")

            # video_path is batch-relative; the serving route accepts it verbatim.
            video_url = f"/api/batch-video/video/{batch_id}/{result.video_path}"
            gen_seconds = None
            if status.start_time and status.end_time:
                gen_seconds = (status.end_time - status.start_time).total_seconds()
            output_lines = [
                f"Video generated successfully" + (f" in {gen_seconds:.0f}s." if gen_seconds else "."),
                f"Prompt: {prompt}",
                f"Frames: {duration_frames} | Steps: {num_inference_steps} | Batch: {batch_id}",
                f"Video: {video_url}",
                f"Open Video Gen: {studio_url}",
            ]
            metadata = {
                "prompt": prompt,
                "batch_id": batch_id,
                "video_url": video_url,
                "generation_time": gen_seconds,
                "studio_url": studio_url,
            }
            if result.thumbnail_path:
                metadata["thumbnail_url"] = f"/api/batch-video/video/{batch_id}/{result.thumbnail_path}"
            return ToolResult(success=True, output="\n".join(output_lines), metadata=metadata)

        except ImportError:
            return ToolResult(success=False, error="Video generation dependencies not available.")
        except Exception as e:
            logger.error(f"VideoGeneratorTool error: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Video generation failed: {str(e)}")


class EditImageTool(BaseTool):
    """Edit an EXISTING image from a natural-language instruction (FLUX.1 Kontext).

    Use when the user SUPPLIES or ATTACHES an image and asks to add/remove/change
    something in it — e.g. 'put a cowboy hat on this character', 'make it night',
    'remove the sign'. Preserves the original's identity/composition and applies
    only the requested change. (Contrast generate_image, which makes a brand-new
    image from text with no input picture.)"""

    name = "edit_image"
    read_only = False
    destructive = False
    description = (
        "Edit an existing image using a natural-language instruction. Use this when "
        "the user has attached/uploaded an image (or names one) and asks to add, "
        "remove, or change something in it, e.g. 'put a cowboy hat on this character'. "
        "Preserves the original subject and only applies the requested edit. If the "
        "user did not attach an image, ask them to attach one. Do NOT use this to make "
        "a brand-new image from scratch — use generate_image for that."
    )
    parameters = {
        "instruction": ToolParameter(
            name="instruction", type="string",
            description="The edit to perform, e.g. 'put a cowboy hat on this character', 'change the shirt to red'.",
            required=True,
        ),
        "image": ToolParameter(
            name="image", type="string",
            description=("Path, URL, or reference of the image to edit. Usually omit this — "
                         "the image the user just attached is used automatically."),
            required=False, default="",
        ),
        "steps": ToolParameter(
            name="steps", type="int",
            description="Diffusion steps (more = higher fidelity, slower). Default 28.",
            required=False, default=28,
        ),
        "model": ToolParameter(
            name="model", type="string",
            description=(
                "Image model/backend. Default follows /imagemodel (Settings). "
                "'kontext' or 'auto' uses FLUX.1 Kontext instruction editing when installed; "
                "other downloaded models (sd-xl, zimage-turbo, …) use img2img."
            ),
            required=False, default="auto",
        ),
    }

    @staticmethod
    def _uses_kontext_backend(model: str) -> bool:
        m = (model or "auto").strip().lower()
        if m in _KONTEXT_MODEL_IDS:
            return True
        if m == "auto":
            try:
                from backend.services.comfyui_image_generator import ComfyUIImageGenerator
                return ComfyUIImageGenerator()._kontext_installed()
            except Exception:
                return False
        return False

    def _edit_via_img2img(
        self, *, src: str, instruction: str, model: str, output_path: str,
    ) -> ToolResult:
        from PIL import Image
        from backend.config import OUTPUT_DIR
        from backend.services.offline_image_generator import get_image_generator
        from backend.services.gpu_resource_policy import gpu_session
        from backend.services.job_operation_gate import GpuBusyError
        from backend.services.job_types import JobKind

        generator = get_image_generator()
        if not generator.service_available:
            return ToolResult(
                success=False,
                error="Image edit service not available (offline pipeline not installed).",
            )
        init_image = Image.open(src)
        width, height = init_image.size
        effective_model = model if model and model != "auto" else "auto"
        try:
            with gpu_session(
                JobKind.VIDEO_RENDER, f"chat_edit_{uuid.uuid4().hex[:8]}",
                on_busy="raise", evict_ollama=True, vram_estimate_mb=11000,
                require_fit=True, cross_process=True,
            ):
                result = generator.generate_image_from_image(
                    prompt=instruction,
                    init_image=init_image,
                    strength=0.35,
                    model=effective_model,
                    width=width,
                    height=height,
                    num_inference_steps=28,
                )
        except GpuBusyError:
            return ToolResult(
                success=False,
                error="GPU is busy with another render right now — try again in a moment.",
            )
        if not result.success or not result.image_path:
            return ToolResult(success=False, error=result.error or "img2img edit failed")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        shutil.copy2(result.image_path, output_path)
        filename = os.path.basename(output_path)
        image_url = f"/api/outputs/generated_images/{filename}"
        return ToolResult(
            success=True,
            output=(
                f"Image edited successfully (img2img, model={result.model_used or effective_model}).\n"
                f"Image URL: {image_url}\nEdit: {instruction}"
            ),
            metadata={
                "image_url": image_url,
                "filename": filename,
                "instruction": instruction,
                "model": result.model_used or effective_model,
                "backend": "img2img",
            },
        )

    def _resolve_image(self, image: str):
        """Resolve a path / URL / data-URI to a local file. None if unresolvable.
        (The common case — the user's attached image — is injected by the chat engine
        as a real disk path, so this is the fallback for explicit paths/URLs.)"""
        if not image:
            return None
        if os.path.exists(image):
            return image
        try:
            from backend.config import OUTPUT_DIR
        except Exception:
            OUTPUT_DIR = "."
        edit_dir = os.path.join(OUTPUT_DIR, "edit_inputs")
        os.makedirs(edit_dir, exist_ok=True)
        # data URI or bare base64 blob
        if image.startswith("data:") or (len(image) > 256 and "/" not in image[:64] and " " not in image[:64]):
            try:
                import base64
                raw = base64.b64decode(image.split(",", 1)[1] if image.startswith("data:") else image)
                p = os.path.join(edit_dir, f"edit_src_{uuid.uuid4().hex[:12]}.png")
                with open(p, "wb") as f:
                    f.write(raw)
                return p
            except Exception:
                return None
        # a served output URL → map back to disk
        if "/api/outputs/" in image:
            cand = os.path.join(OUTPUT_DIR, image.split("/api/outputs/", 1)[1].split("?", 1)[0])
            if os.path.exists(cand):
                return cand
        # OFFLINE-FIRST: never fetch an external URL. A remote image URL (e.g. a
        # files.oaiusercontent.com / CDN link that rode in with the attachment) must
        # NOT trigger an outbound request. Same-host app URLs were already mapped to
        # disk above; anything else is refused, not downloaded.
        if image.startswith("http://") or image.startswith("https://"):
            logger.warning(
                "edit_image: refusing to fetch a non-local image URL (offline-first): %s",
                image[:80],
            )
            return None
        return None

    def execute(self, instruction: str, image: str = "", steps: int = 28,
                model: str = "auto", **kwargs) -> ToolResult:
        src = self._resolve_image(image)
        if not src:
            return ToolResult(
                success=False,
                error="No image to edit. Ask the user to attach the image they want edited.",
            )
        try:
            from backend.config import OUTPUT_DIR
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator
            from backend.utils.settings_utils import get_chat_image_model

            effective_model = (model or "auto").strip() or get_chat_image_model()
            output_dir = os.path.join(OUTPUT_DIR, "generated_images")
            os.makedirs(output_dir, exist_ok=True)
            filename = f"edit_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
            output_path = os.path.join(output_dir, filename)

            if self._uses_kontext_backend(effective_model):
                ComfyUIImageGenerator().edit_image(
                    image_path=src, instruction=instruction,
                    output_path=output_path, steps=int(steps),
                )
                backend = "kontext"
            else:
                img2img_result = self._edit_via_img2img(
                    src=src, instruction=instruction,
                    model=effective_model, output_path=output_path,
                )
                if not img2img_result.success:
                    return img2img_result
                image_url = (img2img_result.metadata or {}).get("image_url")
                return ToolResult(
                    success=True,
                    output=img2img_result.output,
                    metadata={**(img2img_result.metadata or {}), "backend": "img2img"},
                )

            image_url = f"/api/outputs/generated_images/{filename}"
            return ToolResult(
                success=True,
                output=(
                    f"Image edited successfully (kontext).\n"
                    f"Image URL: {image_url}\nEdit: {instruction}"
                ),
                metadata={
                    "image_url": image_url,
                    "filename": filename,
                    "instruction": instruction,
                    "model": effective_model,
                    "backend": backend,
                },
            )
        except Exception as e:
            logger.error(f"EditImageTool error: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Image edit failed: {str(e)}")
