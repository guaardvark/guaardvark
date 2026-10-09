"""Image generation and vision analysis tools for the agent system."""

import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.services import tool_jobs as _tool_jobs
from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.backend_http import BackendError
from backend.utils.backend_http import backend_base_url as _backend_base_url
from backend.utils.backend_http import http_json as _http_json
from backend.utils.backend_http import is_mcp_caller, is_mcp_transport, run_tool_in_backend
from backend.services.job_types import RenderErrorKind, batch_failure, describe_failure, failure_kind, failure_text

logger = logging.getLogger(__name__)

_KONTEXT_MODEL_IDS = frozenset({
    "kontext", "flux-kontext", "flux-kontext-dev", "flux.kontext",
})
_QWEN_EDIT_MODEL_IDS = frozenset({
    "qwen", "qwen-image-edit", "qwen-edit", "qwenimage-edit",
})

# What the media-input parameters accept (rules: backend/utils/media_inputs.py).
_SERVED_FORMS = (
    "a URL an image or edit tool returned (/api/batch-image/image/<batch>/<file> or "
    "/api/outputs/<path>, also with http://host:port in front), a guaardvark://outputs/<path> "
    "resource URI"
)
_PATH_RULE = (
    "Over MCP the file must be in Guaardvark's uploads folder or in an outputs folder MCP "
    "resources serve (what resources/list shows); files named like keys or credentials "
    "(.env, *.pem, *.key, id_rsa* and similar) are refused everywhere."
)
_IMAGE_INPUT_FORMS = f"{_SERVED_FORMS}, a data: URI, or a file path. {_PATH_RULE}"
_VIDEO_INPUT_FORMS = (
    f"a library document id or /api/files/document/<id>/download link, {_SERVED_FORMS}, "
    f"or a file path. {_PATH_RULE}"
)


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
        "Generate a new image from a text prompt on this machine's GPU. Returns the image's "
        "URL, or with wait_for_result=false a batch id to poll with get_generation_status. "
        "Use when the user asks to create, generate, draw or visualize an image. For a "
        "character from the Cast Library pass its numeric id in subject_ids (e.g. "
        "subject_ids=[3]); naming the character only in the prompt does not load its LoRA. "
        "To change an existing picture use edit_image; to cut out its subject, "
        "remove_background; for a video, generate_video."
    )
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            description=(
                "Scene/action description only (pose, lighting, setting). "
                "Do not embed JSON here. For cast characters put identity in subject_ids, "
                "not as the whole prompt body. If quoting on-image text, put EXACT words "
                'in double quotes — e.g. a sign reading "OPEN".'
            ),
            required=True,
        ),
        "subject_ids": ToolParameter(
            name="subject_ids",
            type="list",
            items="int",
            description=(
                "Optional. Numeric Cast Library subject IDs with trained LoRAs to lock "
                "identity (e.g. [3]). Separate parameter — never nest this inside prompt. "
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
            description="Image width in pixels, default 1024: one of 256, 384, 512, 640, 768, 896, 1024, 1280 or 1536. Another value resets both sides to 1024 unless the prompt states that size (e.g. '1280x768'). The model's own limits may clamp the final size; the result reports it.",
            required=False,
            default=1024,
        ),
        "height": ToolParameter(
            name="height",
            type="int",
            description="Image height in pixels, default 1024: one of 256, 384, 512, 640, 768, 896, 1024, 1280 or 1536 (see width).",
            required=False,
            default=1024,
        ),
        "steps": ToolParameter(
            name="steps",
            type="int",
            description="Optional sampling steps. The server may raise it to the model's minimum and will say so.",
            required=False,
            default=None,
        ),
        "model": ToolParameter(
            name="model",
            type="string",
            description=(
                "Model to use. Default 'auto' — recommended; the system auto-picks the best "
                "downloaded model for the prompt (usually Z-Image-Turbo or SDXL). "
                "With subject_ids, keep 'auto': each character renders on the base model of its "
                "own LoRA. A named model must be one every character has a LoRA for, or the "
                "render is refused. "
                "Only override when the user names a specific model: 'krea2-turbo', 'zimage-turbo', "
                "'sd-xl', 'sdxl-turbo', 'realistic-vision', 'epic-realism', 'sana-sprint', "
                "'sana-sprint-0.6b'. A model that is not "
                "installed is refused with where to install it; nothing is downloaded."
            ),
            required=False,
            default="auto",
        ),
        "wait_for_result": ToolParameter(
            name="wait_for_result",
            type="bool",
            description=(
                "true: render now and return the image (the default in Guaardvark's chat). "
                "false: queue the render as an image batch and return its batch id at once; "
                "poll get_generation_status for the file (the default over MCP)."
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
                    image_model=model,
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
                notice_line = f"{meta['steps_notice']}\n" if meta.get("steps_notice") else ""
                return ToolResult(
                    success=True,
                    output=(
                        f"Image generated successfully in {still.generation_time:.1f}s.\n"
                        f"Image URL: {image_url}\n"
                        f"Prompt used: {still.prompt_used}\n"
                        f"Style: {style}\n"
                        f"Size: {still.width}x{still.height}\n"
                        f"Steps: {still.steps}\n"
                        f"{notice_line}"
                        f"CFG: {still.guidance}\n"
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
                        "steps_requested": meta.get("steps_requested"),
                        "steps_notice": meta.get("steps_notice"),
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
        from backend.services.stills_defaults import resolve_stills_defaults
        sampling = resolve_stills_defaults(model, width=width, height=height, steps=steps, guidance=guidance)
        params = {
            "model": model or "auto",
            "steps_explicit": False,
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
        notes: list = []
        try:
            if via_http:
                data = _http_json("POST", "/api/batch-image/generate/prompts",
                                  {"prompts": [prompt], **params})
                batch_id = data["batch_id"]
                # The route is the authority on what was queued: its model, steps and warnings.
                served = data.get("parameters") or {}
                sampling.update({k: v for k, v in served.items()
                                 if k in ("steps", "steps_requested", "steps_notice")})
                model = served.get("model") or model
                notes = [str(w) for w in (data.get("validation") or {}).get("warnings") or []]
            else:
                from backend.services.batch_image_generator import start_batch_from_prompts
                batch_id = start_batch_from_prompts([prompt], **params)
        except ImportError:
            return ToolResult(success=False, error="Batch image generation is not available.")
        except Exception as e:
            logger.error("ImageGeneratorTool queue failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"Could not queue the image: {e}")
        if wait:
            return self._wait_http(batch_id, prompt, sampling)
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
                f"Steps: {sampling['steps']} (planned)",
                *([sampling["steps_notice"]] if sampling.get("steps_notice") else []),
                *[f"Note: {note}" for note in notes],
                cast_line,
                f"Poll: get_generation_status(batch_id=\"{batch_id}\")",
                f"Open Images: {self.STUDIO_URL}",
            ]),
            metadata={
                "prompt": prompt,
                "batch_id": batch_id,
                "queued": True,
                "warnings": notes,
                "steps": sampling["steps"],
                "steps_requested": sampling.get("steps_requested"),
                "steps_notice": sampling.get("steps_notice"),
                "studio_url": self.STUDIO_URL,
                "status_tool": "get_generation_status",
                "width": width,
                "height": height,
                "model": model or "auto",
                "subject_ids": list(subject_ids or []),
            },
        )


    def _wait_http(self, batch_id: str, prompt: str, sampling: dict | None = None) -> ToolResult:
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
                        f"Steps: {f.get('steps') if f.get('steps') is not None else 'unknown'}",
                        *([f["steps_notice"]] if f.get("steps_notice") else []),
                        f"Batch: {batch_id}",
                    ]),
                    metadata={"image_url": f["url"], "batch_id": batch_id, "prompt": prompt,
                              "model": f.get("model"), "generation_time": f.get("generation_time"),
                              "steps": f.get("steps"), "steps_requested": f.get("steps_requested"),
                              "steps_notice": f.get("steps_notice")},
                )
            if info.get("status") in ("error", "cancelled") or (
                info.get("status") == "completed" and not info.get("files")
            ):
                err = info.get("error") or "; ".join(info.get("errors") or []) or info.get("status")
                return ToolResult(success=False, error=f"Image generation failed: {err}")
        return ToolResult(
            success=True,
            output=f"Image still rendering after {self.MAX_WAIT_S // 60} minutes (batch {batch_id}). "
                   f"Poll get_generation_status(batch_id=\"{batch_id}\"); it is not a failure.\n"
                   f"Steps: {(sampling or {}).get('steps', 'unknown')} (planned)"
                   + (f"\n{sampling['steps_notice']}" if (sampling or {}).get("steps_notice") else ""),
            metadata={"batch_id": batch_id, "queued": True, "still_running": True, "prompt": prompt},
        )


def _quality_summary(quality) -> Optional[dict]:
    """The flags a finished clip carries, for status readers."""
    if not isinstance(quality, dict):
        return None
    flags = [f for f in quality.get("flags") or [] if isinstance(f, dict) and f.get("message")]
    summary = {"checked": bool((quality.get("frames") or {}).get("readable")) or bool(flags), "flags": flags}
    review = quality.get("vlm_review")
    if isinstance(review, dict) and review.get("status") == "not_reviewed":
        summary["not_reviewed"] = review.get("message") or review.get("reason") or "no score"
    return summary


def _review_line(review, studio_url: Optional[str]) -> Optional[str]:
    """Where a held clip stands, for status readers (batch_video_generator review)."""
    state = (review or {}).get("state") if isinstance(review, dict) else None
    if state == "needs_review":
        where = f" in Video Gen ({studio_url})" if studio_url else " in Video Gen"
        return ("Review: needs review — held until a person approves or re-renders it"
                f"{where}; nothing downstream uses it before then")
    if state == "approved":
        return "Review: approved by a person after a quality flag"
    if state == "rerendered":
        return f"Review: re-rendered as {review.get('rerender_batch_id')}"
    return None


# get_generation_status can wait for a job, for clients that cannot pause between
# checks. Kept under the 60 s per-call limit common MCP clients apply (Codex, opencode).
MAX_STATUS_WAIT_S = 50
STATUS_POLL_S = 3
_ACTIVE_JOB_STATUSES = {"queued", "pending", "running", "processing", "start", "in_progress"}


# ---- tool jobs: photo edits and animations called over MCP ----------------------------------
# These tools can run longer than an MCP client waits for one call. An MCP call
# runs as a tool job in the backend (backend/services/tool_jobs.py) and answers
# with a job id for get_generation_status; chat and the Studio run them inline.

# What each tool's job is called in get_generation_status.
_TOOL_JOB_KINDS = {
    "edit_image": "image edit",
    "inpaint_image": "inpaint",
    "outpaint_image": "outpaint",
    "remove_background": "background removal",
    "generate_animation": "animation",
}

# Sentence for the descriptions of the tools that run as tool jobs over MCP.
_TOOL_JOB_NOTE = (
    " Over MCP the call returns a job id at once; poll get_generation_status with it for the "
    "file, unless wait_for_result is true."
)


def _wait_for_result_param() -> ToolParameter:
    return ToolParameter(
        name="wait_for_result", type="bool", required=False, default=False,
        description=(
            "Over MCP: false (default) starts the work as a job and returns its job id at once; poll "
            "get_generation_status with it for the file. true waits up to "
            f"{_tool_jobs.MAX_WAIT_S:.0f} s (half the server's MCP timeout when that is shorter) and "
            "returns the result, or the job id if it is still running. In chat the tool always runs "
            "inline and this is ignored."
        ),
    )


def _truthy(value) -> bool:
    return str(value).lower() in ("1", "true", "yes")


def _job_queued_result(snapshot: dict, waited_s: Optional[float] = None) -> ToolResult:
    job_id = snapshot["job_id"]
    kind = _TOOL_JOB_KINDS.get(snapshot["tool"], snapshot["tool"])
    kind = kind[0].upper() + kind[1:]
    if waited_s is None:
        head = f"{kind} queued as job {job_id}."
    else:
        head = f"{kind} job {job_id} is still {snapshot.get('status') or 'running'} after {waited_s:.0f} s."
    return ToolResult(
        success=True,
        output="\n".join([
            head,
            f"Poll: get_generation_status(batch_id=\"{job_id}\") for the file. The job runs in the "
            "Guaardvark backend; closing this client does not stop it.",
        ]),
        metadata={"job_id": job_id, "queued": True, "tool": snapshot["tool"],
                  "status_tool": "get_generation_status"},
    )


def _job_tool_result(snapshot: dict) -> Optional[ToolResult]:
    """A finished job's own ToolResult, or None while it is queued or running."""
    if snapshot.get("status") not in ("done", "failed"):
        return None
    result = snapshot.get("result") or {}
    return ToolResult(
        success=bool(result.get("success")),
        output=result.get("output"),
        error=result.get("error"),
        metadata={**(result.get("metadata") or {}), "job_id": snapshot["job_id"]},
    )


def _run_or_queue(tool: BaseTool, run) -> ToolResult:
    """``run()`` now, or as a tool job when an MCP client made the call.

    The MCP server forwards these tools to the backend (``_forward_tool_job``),
    where the call is marked as an MCP client's (``is_mcp_caller``). By the
    time this runs the tool has checked its inputs, so a bad image is refused
    at once rather than as a failed job. Every other caller, chat included,
    gets ``run()`` inline.
    """
    if not is_mcp_caller(tool) or _tool_jobs.in_job():
        return run()
    try:
        return _job_queued_result(_tool_jobs.submit(tool.name, run))
    except _tool_jobs.ToolJobsBusy as e:
        return ToolResult(success=False, error=str(e))


def _forward_tool_job(tool: BaseTool, arguments: dict, wait_for_result) -> ToolResult:
    """In the MCP server: start the call as a tool job in the backend.

    The backend answers with the job id at once. With ``wait_for_result`` this
    process then waits for the job up to ``tool_jobs.wait_seconds()``, which
    follows this server's own call timeout, and returns the job's result or
    its id.
    """
    started = run_tool_in_backend(tool.name, {k: v for k, v in arguments.items() if v is not None})
    job_id = (started.metadata or {}).get("job_id")
    if not _truthy(wait_for_result) or not started.success or not job_id:
        return started
    wait_s = _tool_jobs.wait_seconds()
    try:
        snapshot = _http_json("GET", f"/api/tools/jobs/{job_id}?wait_s={wait_s:g}", timeout=wait_s + 30)
    except RuntimeError as e:
        logger.warning("waiting for tool job %s: %s", job_id, e)
        return started
    return _job_tool_result(snapshot) or _job_queued_result(snapshot, waited_s=wait_s)


class GenerationStatusTool(BaseTool):
    """Read the state of a queued image or video batch, bulk CSV job, song or tool job by id."""

    name = "get_generation_status"
    read_only = True
    idempotent = True
    description = (
        "Report the state of a queued generation: an image batch (ImageBatch_...) from "
        "generate_image with wait_for_result=false or started in the Studio, a video batch "
        "from generate_video, a bulk CSV job (bulk_gen_...) from generate_bulk_csv, a song "
        "(a 32-character job id) from generate_music, or a tool job (tooljob_...) from edit_image, "
        "inpaint_image, outpaint_image, remove_background or generate_animation called over MCP. "
        "Returns status (a tool job is queued, running, done or failed, with the time since it "
        "started), progress while running (a video batch names its pipeline stage, such as gpu_wait "
        "or generate, with the share of clips finished), and each finished file (URL for images, video and "
        "animations; for a CSV, its path and how many rows it holds; for a song, its download "
        "link), or the error of a failed job. Use after a queued generate call, or when the user "
        "asks whether a render is done. Read-only; an unknown id is an error, and so is a backend "
        "that does not answer (over MCP the backend must be running)."
    )
    parameters = {
        "batch_id": ToolParameter(
            name="batch_id",
            type="string",
            description=("The id a generate or edit tool returned, e.g. ImageBatch_09-11-2026_132620_013, "
                         "bulk_gen_1790556697_3fa2c1, a song's job id or tooljob_3fa2c1_0123456789ab."),
            required=True,
        ),
        "wait_seconds": ToolParameter(
            name="wait_seconds",
            type="int",
            required=False,
            default=0,
            minimum=0,
            maximum=MAX_STATUS_WAIT_S,
            description=(f"Wait up to this many seconds (0-{MAX_STATUS_WAIT_S}) for the job to finish before "
                         "answering, instead of reporting 'still running' at once. Useful for a client that "
                         "cannot pause between checks. Default 0: answer immediately."),
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
                    **{k: (r.metadata or {}).get(k) for k in ("steps", "steps_requested", "steps_notice")},
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
                    **{k: (r.get("metadata") or {}).get(k) for k in ("steps", "steps_requested", "steps_notice")},
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
                entry["quality"] = _quality_summary((r.get("metadata") or {}).get("quality"))
                entry["review"] = r.get("review")
                files.append(entry)
        failed = [r.get("error") for r in (d.get("results") or []) if not r.get("success") and r.get("error")]
        failures = [r.get("failure") or describe_failure(r.get("error_kind"), r.get("error"))
                    for r in (d.get("results") or []) if not r.get("success")]
        return {
            "kind": "video", "batch_id": batch_id, "status": d.get("status"), "stage": d.get("stage"),
            "progress": d.get("progress_pct"), "current_item": d.get("current_item"),
            "completed": d.get("completed_videos"), "failed": len(failed), "total": d.get("total_videos"),
            "error": d.get("error"), "errors": failed, "files": files,
            "failure": d.get("failure"), "failures": failures,
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
                entry["quality"] = _quality_summary((getattr(r, "metadata", None) or {}).get("quality"))
                entry["review"] = getattr(r, "review", None)
                files.append(entry)
        failed = [r.error for r in (status.results or []) if not r.success and r.error]
        failures = [describe_failure(getattr(r, "error_kind", None), r.error)
                    for r in (status.results or []) if not r.success]
        completed = sum(1 for r in (status.results or []) if r.success)
        return {
            "kind": "video",
            "batch_id": batch_id,
            "status": status.status,
            "stage": getattr(status, "stage", None),
            "progress": getattr(status, "progress_pct", None),
            "current_item": getattr(status, "current_item", None),
            "completed": completed,
            "failed": len(failed),
            "total": getattr(status, "total_videos", None),
            "error": getattr(status, "error", None),
            "errors": failed,
            "files": files,
            "failure": batch_failure(status),
            "failures": failures,
            "studio_url": f"/video?batch={batch_id}",
        }

    @staticmethod
    def _bulk_info(job_id: str, progress: dict, output_filename, target_rows=None):
        from backend.config import GUAARDVARK_ROOT, OUTPUT_DIR
        status = {"start": "running", "processing": "running", "complete": "complete",
                  "error": "error", "cancelled": "cancelled"}.get(progress.get("status"), progress.get("status"))
        message = progress.get("message")
        files = []
        if status == "complete" and output_filename:
            out_dir = Path(OUTPUT_DIR).resolve()
            path = out_dir / output_filename
            if path.is_file():
                import csv
                with open(path, encoding="utf-8", newline="") as f:
                    rows = max(0, sum(1 for _ in csv.reader(f)) - 1)
                root = Path(GUAARDVARK_ROOT).resolve()
                shown = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
                files = [{"url": shown}]
                message = f"{rows} row(s) written" + (f" of {target_rows} asked for" if target_rows else "")
            else:
                status, message = "error", ("The job ended but its CSV is not in the outputs folder; a CSV that "
                                            "fails validation is removed, and the Bulk Generation page shows why.")
        return {
            "kind": "bulk CSV", "batch_id": job_id, "status": status,
            "progress": progress.get("progress"), "message": message,
            "error": message if status == "error" else None, "errors": [],
            "files": files, "studio_url": "/file-generation",
        }

    @classmethod
    def _bulk_status_tracking(cls, job_id: str):
        """A finished bulk job leaves the in-memory progress list about a minute after
        it ends; the tracking file it writes under OUTPUT_DIR/tracking stays."""
        if not job_id.startswith("bulk_gen_"):
            return None
        import glob
        from backend.config import OUTPUT_DIR
        found = sorted(glob.glob(os.path.join(OUTPUT_DIR, "tracking", f"{glob.escape(job_id)}_tracking_*.json")))
        if not found:
            return None
        with open(found[-1], encoding="utf-8") as f:
            meta = (json.load(f) or {}).get("metadata") or {}
        name = (meta.get("job_parameters") or {}).get("output_filename")
        if meta.get("status") == "completed":
            return cls._bulk_info(job_id, {"status": "complete", "progress": 100}, name, meta.get("target_row_count"))
        info = cls._bulk_info(job_id, {"status": "error", "message": (
            "The job stopped before finishing (the backend may have restarted); "
            "see the Bulk Generation page.")}, None)
        info["stopped"] = True
        return info

    @classmethod
    def _bulk_status(cls, job_id: str):
        if not job_id.startswith("bulk_gen_"):
            return None
        from backend.utils.unified_progress_system import get_unified_progress
        process = get_unified_progress().get_process(job_id)
        if process is None:
            return None
        progress = {"status": process.status.value, "progress": process.progress, "message": process.message}
        data = process.additional_data or {}
        return cls._bulk_info(job_id, progress, data.get("output_filename"), data.get("num_items"))

    @classmethod
    def _bulk_status_http(cls, job_id: str):
        if not job_id.startswith("bulk_gen_"):
            return None
        try:
            d = _http_json("GET", f"/api/bulk-generate/status/{job_id}")
        except RuntimeError as e:
            if "not found" in str(e).lower() or "404" in str(e):
                return None
            raise
        if not d or not d.get("progress_status"):
            return None
        return cls._bulk_info(job_id, d["progress_status"], d.get("output_filename"), d.get("num_items"))

    _AUDIO_JOB_ID = re.compile(r"^[0-9a-f]{32}$")

    @staticmethod
    def _audio_info(job_id: str, job: dict):
        """A song or voice job from Audio Foundry, in this tool's shape."""
        from backend.tools.audio_tools import STUDIO_URL, _file_entry
        status = {"done": "complete", "error": "error", "failed": "error"}.get(job.get("status"), job.get("status"))
        prog = job.get("progress") or {}
        pct = None
        if prog.get("total"):
            pct = int(100 * (prog.get("current") or 0) / prog["total"])
        files = []
        notes = []
        if status == "complete" and job.get("result"):
            result = job["result"]
            entry = _file_entry(result)
            entry.setdefault("url", entry["file"])
            files.append(entry)
            # The output line is a URL; the file's own name and library id
            # are what a person looks for in the library.
            saved = f"Saved as {entry['file']}"
            if entry.get("document_id"):
                saved += f" (library document {entry['document_id']})"
            notes.append(saved)
            if entry.get("note"):
                notes.append(entry["note"])
            meta = result.get("meta") or {}
            if job.get("intent") == "voice" and meta.get("backend"):
                notes.append(f"Engine: {meta['backend']}" + (f", voice {meta['voice']}" if meta.get("voice") else ""))
        return {
            "kind": {"music": "song", "voice": "speech", "fx": "sound effect"}.get(job.get("intent"), "audio"),
            "batch_id": job_id, "status": status, "progress": pct, "message": prog.get("stage") or "",
            "error": job.get("error"), "errors": [], "files": files, "studio_url": STUDIO_URL,
            "note": ". ".join(notes) or None,
        }

    @staticmethod
    def _audio_foundry_stopped(job_id: str):
        """The answer for an audio job id while Audio Foundry is stopped: the
        backend answered, the plugin that holds the job did not, and retrying
        will not change that until someone starts it."""
        from backend.tools.audio_tools import STUDIO_URL
        return {
            "kind": "audio", "batch_id": job_id, "status": "unknown", "progress": None,
            "error": ("Audio Foundry is not running (start it in Plugins, or POST "
                      "/api/plugins/audio_foundry/start), so this job cannot be read. A job that was "
                      "running when it stopped is marked interrupted when it starts again; a finished "
                      "file is already in the library."),
            "errors": [], "files": [], "studio_url": STUDIO_URL,
        }

    @classmethod
    def _audio_status_http(cls, job_id: str):
        if not cls._AUDIO_JOB_ID.match(job_id):
            return None
        from backend.tools.audio_tools import plugin_stopped
        try:
            job = _http_json("GET", f"/api/audio-foundry/jobs/{job_id}")
        except RuntimeError as e:
            if plugin_stopped(e):
                return cls._audio_foundry_stopped(job_id)
            if "not found" in str(e).lower() or "unknown job" in str(e).lower() or "404" in str(e):
                return None
            raise
        return cls._audio_info(job_id, job) if isinstance(job, dict) and job.get("status") else None

    @classmethod
    def _audio_status(cls, job_id: str):
        if not cls._AUDIO_JOB_ID.match(job_id):
            return None
        import requests
        from backend.api.audio_foundry_api import AUDIO_FOUNDRY_URL
        from backend.services import comfyui_music_generator as m3
        m3_job = m3.job_status(job_id)
        if m3_job is not None:
            job = {"intent": "music", "status": {"failed": "error"}.get(m3_job["status"], m3_job["status"]),
                   "error": m3_job.get("error"),
                   "result": {"path": m3_job.get("path"), "document_id": m3_job.get("document_id"),
                              "duration_s": m3_job.get("seconds")} if m3_job.get("path") else None}
            return cls._audio_info(job_id, job)
        try:
            resp = requests.get(f"{AUDIO_FOUNDRY_URL}/jobs/{job_id}", timeout=5)
        except requests.ConnectionError:
            return cls._audio_foundry_stopped(job_id)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return cls._audio_info(job_id, resp.json())

    @staticmethod
    def _tool_job_info(snapshot: dict):
        """A tool job (backend/services/tool_jobs.py) in this tool's shape."""
        result = snapshot.get("result") or {}
        meta = result.get("metadata") or {}
        status = snapshot.get("status")
        files = []
        if status == "done":
            for key in ("image_url", "gif_url", "video_url"):
                url = meta.get(key)
                if url and all(f["url"] != url for f in files):
                    files.append({"url": url})
        tool = snapshot.get("tool") or "tool"
        return {
            "kind": _TOOL_JOB_KINDS.get(tool, tool), "noun": "job", "tool": tool,
            "batch_id": snapshot.get("job_id"), "status": status, "note": snapshot.get("note"),
            "elapsed_s": snapshot.get("elapsed_s"),
            "output": result.get("output") if status == "done" else None,
            "error": result.get("error") if status == "failed" else None,
            "errors": [], "files": files, "studio_url": None,
        }

    @classmethod
    def _tool_job_status(cls, job_id: str):
        if not _tool_jobs.is_job_id(job_id):
            return None
        snapshot = _tool_jobs.get(job_id)
        if snapshot is None:
            return {"missing": _tool_jobs.missing(job_id)[1]}
        return cls._tool_job_info(snapshot)

    @classmethod
    def _tool_job_status_http(cls, job_id: str):
        if not _tool_jobs.is_job_id(job_id):
            return None
        try:
            snapshot = _http_json("GET", f"/api/tools/jobs/{job_id}")
        except BackendError as e:
            if e.status != 404:
                raise
            if isinstance(e.body, dict) and e.body.get("reason"):
                return {"missing": str(e)}
            return {"missing": (f"The Guaardvark backend does not know tool jobs yet ({e}); restart it "
                                "so it runs the same version as this MCP server.")}
        return cls._tool_job_info(snapshot)

    def _read(self, batch_id: str):
        """(info, unreachable): the job from whichever reader knows its id."""
        info = None
        unreachable = None
        remote = self._context.get("transport") == "mcp"
        if _tool_jobs.is_job_id(batch_id):
            readers = (self._tool_job_status_http,) if remote else (self._tool_job_status,)
        else:
            readers = (
                (self._bulk_status_http, self._bulk_status_tracking, self._audio_status_http,
                 self._image_status_http, self._video_status_http)
                if remote else
                (self._bulk_status, self._bulk_status_tracking, self._audio_status,
                 self._image_status, self._video_status)
            )
        for reader in readers:
            try:
                info = reader(batch_id)
            except ImportError:
                continue
            except Exception as e:
                logger.warning("get_generation_status %s via %s: %s", batch_id, reader.__name__, e)
                if reader.__name__.endswith("_http"):
                    unreachable = unreachable or e
                continue
            if info is not None:
                break
        return info, unreachable

    def execute(self, batch_id: str, wait_seconds: int = 0, **kwargs) -> ToolResult:
        batch_id = (batch_id or "").strip()
        if not batch_id:
            return ToolResult(success=False, error="batch_id is required")
        try:
            wait_seconds = max(0, min(int(wait_seconds or 0), MAX_STATUS_WAIT_S))
        except (TypeError, ValueError):
            wait_seconds = 0
        deadline = time.monotonic() + wait_seconds
        info, unreachable = self._read(batch_id)
        # A reader that found the job running is enough to wait on, whatever an
        # earlier reader for another kind of job answered.
        while (info is not None
               and str(info.get("status")).lower() in _ACTIVE_JOB_STATUSES
               and time.monotonic() + STATUS_POLL_S < deadline):
            time.sleep(STATUS_POLL_S)
            info, unreachable = self._read(batch_id)
        # Without an answer from the backend a live job cannot be told from a lost one,
        # so neither "stopped" nor "no such batch" is reported.
        if unreachable is not None and (info is None or info.get("stopped")):
            return ToolResult(success=False, error=(
                f"Could not read {batch_id}: the Guaardvark backend did not answer ({unreachable}). "
                "Try again shortly."))
        if info is None:
            return ToolResult(success=False, error=f"No image, video, audio or bulk CSV job named {batch_id}")
        if info.get("missing"):
            return ToolResult(success=False, error=info["missing"])
        total = info.get("total")
        done = info.get("completed") or 0
        noun = info.get("noun") or (
            "job" if info["kind"] in ("song", "speech", "sound effect", "audio") else "batch")
        head = f"{info['kind'][0].upper()}{info['kind'][1:]} {noun} {batch_id}: {info['status']}"
        if total:
            head += f" ({done}/{total} finished"
            if info.get("failed"):
                head += f", {info['failed']} failed"
            head += ")"
        lines = [head]
        for f in info["files"]:
            lines.append(f"File: {f['url']}")
            if f.get("download"):
                lines.append(f"Download: {f['download']}")
            if f.get("duration_s") is not None:
                lines.append(f"Length: {f['duration_s']} s")
            if info["kind"] == "bulk CSV" and info.get("message"):
                lines.append(info["message"])
            if info["kind"] == "image":
                lines.append(f"Steps: {f.get('steps') if f.get('steps') is not None else 'unknown'}")
                if f.get("steps_notice"):
                    lines.append(f["steps_notice"])
            quality = f.get("quality")
            if quality and quality.get("flags"):
                lines.append("Quality: flagged — " + "; ".join(q["message"] for q in quality["flags"]))
            elif quality and quality.get("checked"):
                lines.append("Quality: no problems found in the sampled frames")
            if quality and quality.get("not_reviewed"):
                lines.append(f"Vision review: not reviewed — {quality['not_reviewed']}")
            review_line = _review_line(f.get("review"), info.get("studio_url"))
            if review_line:
                lines.append(review_line)
        failures = info.get("failures")
        if failures is not None:
            # Video: every failure with its kind, the batch's own first.
            batch = info.get("failure")
            if batch and info.get("error"):
                lines.append("Error: " + failure_text(batch["kind"], batch["message"]))
            for f in failures:
                lines.append("Failed item: " + failure_text(f["kind"], f["message"]))
        else:
            if info.get("error"):
                lines.append(f"Error: {info['error']}")
            for err in info.get("errors") or []:
                lines.append(f"Failed item: {err}")
        # A video batch names its pipeline stage (gpu_wait, keyframe, generate, post, ...);
        # its percentage counts finished clips, so the stage is what moves during one clip.
        stage = info.get("stage") if str(info["status"]).lower() in _ACTIVE_JOB_STATUSES else None
        if info.get("progress") is not None and info["status"] == "running":
            detail = f"stage: {stage}" if stage else (info.get("message") or "")
            lines.append(f"Progress: {info['progress']}% — {detail}".rstrip(" —"))
        elif stage:
            lines.append(f"Stage: {stage}")
        if info.get("elapsed_s") is not None:
            lines.append(f"Elapsed: {info['elapsed_s']:.0f} s")
        if info.get("note"):
            lines.append(info["note"])
        if info.get("output"):
            lines.append(str(info["output"]))
        if info["status"] in ("queued", "pending", "running"):
            lines.append("Still running; poll again in a few seconds, or pass wait_seconds to wait here.")
        if info.get("studio_url"):
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
        "motion description: frame 1 from the prompt, each later frame by img2img on "
        "a downloaded image model that supports it (Z-Image Turbo, SDXL or Stable "
        "Diffusion, picked automatically). Use when the user asks to animate, create "
        "a GIF, or make a looping frame morph. For a cinema clip from a video model "
        "use generate_video instead." + _TOOL_JOB_NOTE
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
        "wait_for_result": _wait_for_result_param(),
    }

    def __init__(self):
        super().__init__()

    def execute(self, prompt: str, motion: str, frames: int = 8,
                strength: float = 0.20, format: str = "both",
                vision_steering: bool = False, wait_for_result: bool = False, **kwargs) -> ToolResult:
        logger.info(f"AnimationGeneratorTool: prompt={prompt[:60]}..., motion={motion}, frames={frames}")

        if is_mcp_transport(self):
            # The frames render on the GPU, which belongs to the backend process.
            return _forward_tool_job(self, {
                "prompt": prompt, "motion": motion, "frames": frames, "strength": strength,
                "format": format, "vision_steering": vision_steering,
            }, wait_for_result)
        return _run_or_queue(self, lambda: self._animate(
            prompt, motion, frames=frames, strength=strength, format=format,
            vision_steering=vision_steering,
        ))

    def _animate(self, prompt: str, motion: str, *, frames: int, strength: float,
                 format: str, vision_steering: bool) -> ToolResult:
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

            from backend.services.gpu_resource_policy import gpu_session, gpu_session_when_free
            from backend.services.job_operation_gate import GpuBusyError
            from backend.services.job_types import JobKind
            from backend.services.offline_image_generator import get_image_generator
            # A tool job waits its turn for a busy GPU; chat is told at once.
            gpu_wait = _job_gpu_wait()
            try:
                # Use the image gen's ram estimate for the animation (reuses SD pipeline)
                img_gen = get_image_generator()
                ram_est = img_gen._ram_estimate_gb(request.model) if hasattr(img_gen, "_ram_estimate_gb") else 6.0
                op_id = f"chat_anim_{uuid.uuid4().hex[:8]}"
                session_kwargs = dict(evict_ollama=True, vram_estimate_mb=8000,
                                      ram_estimate_gb=ram_est, require_fit=True, cross_process=True)
                if gpu_wait:
                    claim = gpu_session_when_free(
                        JobKind.VIDEO_RENDER, op_id, wait_s=gpu_wait["wait_s"],
                        on_wait=gpu_wait["on_wait"], should_stop=gpu_wait["should_stop"],
                        **session_kwargs,
                    )
                else:
                    claim = gpu_session(JobKind.VIDEO_RENDER, op_id, on_busy="raise", **session_kwargs)
                with claim:
                    result = anim_gen.generate(request)
            except GpuBusyError as e:
                return ToolResult(success=False, error=_gpu_refusal(e, gpu_wait) or (
                    "GPU is busy with another render right now — try again in a moment."))

            if result.success:
                output_lines = [
                    f"Animation generated successfully in {result.generation_time:.1f}s.",
                    f"Frames: {result.frame_count} | FPS: {request.fps}",
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


def _ref_list(value) -> list:
    """A reference argument as a list of non-empty strings: a list, one
    string, or a comma-separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value if str(v or "").strip()]


def _default_reference_prompt(prompt: str, refs: dict, params: dict) -> str:
    """The reference build's prompt for a tool call, which carries no roles:
    pictures and clips are kept as shown, each clip's own sound comes with
    it, and audio is a sound reference. Compiled before queuing, as the
    Studio route does; Verbatim Prompts drops only the style opening."""
    from backend.services import h3_prompt_compiler as h3
    from backend.services.comfyui_video_generator import _media_has_audio
    from backend.services.media_director import verbatim_prompts_enabled
    spec = {
        "images": [{"role": "keep"} for _ in refs["ref_images"]],
        "videos": [{"role": "subject", "soundtrack": _media_has_audio(v["path"]) is not False}
                   for v in refs["ref_videos"]],
        "audios": [{"role": "sound"} for _ in refs["ref_audios"]],
    }
    fps = float(params.get("fps") or 24)
    style = None if verbatim_prompts_enabled() else params.get("prompt_style") or "cinematic"
    intent = h3.intent_from_references(prompt, params["duration_frames"] / fps, spec, style=style)
    return h3.compile(intent)[0]


def _media_input(ref, *, mcp: bool, label: str):
    """A generate_video input (document id, served URL, resource URI or path)
    as a MediaRef, under the shared media-input rules."""
    from backend.utils.media_inputs import document_file, resolve_media_ref
    return resolve_media_ref(ref, mcp=mcp, label=label, document_path=document_file)


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
        "animate between frames; reference_images, reference_clips and "
        "reference_audio lock a person, look, motion or voice on the reference "
        "build (picked automatically when no model is named). For short looping "
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
            description="Video model id from the registry (e.g. wan22-5b, minimax-h3-int8). Omitted: the active video model in Settings, or on a machine where none is set the best installed one for the GPU; when that model cannot make this clip the call is refused with the reason, never rendered on another model. The result names the model used.",
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
            description=("Inference steps. Omit to use the model's default, or the speed profile's count when "
                         "speed_profile is given. A value below the floor (the speed profile's when one is "
                         "given, else the model's) is raised to it."),
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
            description=f"First frame (image-to-video): {_VIDEO_INPUT_FORMS}",
            required=False,
        ),
        "last_image": ToolParameter(
            name="last_image",
            type="string",
            description=("Last frame; needs a model with first+last-frame mode. Same forms as "
                         "first_image."),
            required=False,
        ),
        "reference_images": ToolParameter(
            name="reference_images",
            type="list",
            items="string",
            description=("Reference images (identity, look), each as a string in the same forms "
                         "as first_image; needs the reference build."),
            required=False,
        ),
        "reference_clips": ToolParameter(
            name="reference_clips",
            type="list",
            items="string",
            description=("Reference video clips (subject, motion, a clip to edit or continue), 2-15 s "
                         "each, in the same forms as first_image; a clip's own sound comes with it. "
                         "Named <Video 1>, <Video 2> in the prompt; needs the reference build."),
            required=False,
        ),
        "reference_audio": ToolParameter(
            name="reference_audio",
            type="list",
            items="string",
            description=("Voice or music references (up to 3), each in the same forms as first_image "
                         "(a generated song's document id works); a single string also works. Needs "
                         "the reference build."),
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
            description=(
                "Prompt style: cinematic, realistic, artistic, anime, 3d_animation, stop_motion, "
                "hand_drawn, western_cartoon, none. A model may not offer every style; its "
                "capability record lists prompt_styles."
            ),
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
                        reference_images: Optional[list] = None, reference_audio=None,
                        speed_profile: Optional[str] = None, style: Optional[str] = None,
                        reference_clips: Optional[list] = None) -> tuple:
        """Turn the tool's arguments into batch parameters checked against the
        model's capability record. Returns (params, None) or (None, message).
        Pure: no service is touched, so the rules are testable."""
        from backend.services.video_model_registry import (
            GENERATION_TYPES, VIDEO_MODEL_REGISTRY, model_capabilities, i2v_model_for,
            resolve_active_video_model,
        )
        refs = _ref_list(reference_images)
        clips = _ref_list(reference_clips)
        audios = _ref_list(reference_audio)
        model_id = (model or "").strip()
        if not model_id:
            # The resolver never swaps families; its refusal is the answer,
            # not a cue to render on some other model.
            role = "ref2v" if (refs or clips or audios) else "i2v" if first_image else "t2v"
            picked, resolve_err = resolve_active_video_model(role, comfyui_down_ok=True)
            if not picked:
                return None, resolve_err or (
                    "No video model is chosen or installed. Pass model, or set the active "
                    "video model in Settings."
                )
            model_id = picked
        entry = VIDEO_MODEL_REGISTRY.get(model_id)
        if not entry:
            known = ", ".join(k for k, e in VIDEO_MODEL_REGISTRY.items() if e.get("type") in GENERATION_TYPES)
            return None, f"Unknown video model '{model_id}'. Known: {known}."
        caps = model_capabilities(model_id)
        if not caps:
            return None, f"'{model_id}' is a companion file, not a video model."
        if entry.get("type") not in GENERATION_TYPES:
            # The song model carries a capability record too; a render would
            # route its id to a video family that shares the prefix.
            return None, f"{entry['name']} generates audio, not video. Use generate_music for a song."
        if audio and not caps.get("audio_out"):
            return None, (
                f"{entry['name']} renders silent clips. For a clip with its own soundtrack use "
                f"a model that declares audio, such as minimax-h3-int8."
            )
        if (refs or clips or audios) and "ref2v" not in caps["modes"]:
            return None, (
                f"{entry['name']} takes no reference images, clips or audio; use the reference build "
                f"(minimax-h3-ref2va-int8) for identity, look, motion or voice references."
            )
        if "ref2v" in caps["modes"]:
            from backend.services.comfyui_video_generator import reference_count_error
            count_err = reference_count_error(caps.get("ref_limits") or {}, entry["name"], refs,
                                              [{"path": c} for c in clips], audios)
            if count_err:
                return None, count_err
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

        profiles = caps.get("speed_profiles") or {}
        if speed_profile and speed_profile not in profiles:
            declared = ", ".join(profiles) or "none"
            return None, f"{entry['name']} declares no speed profile '{speed_profile}' (declared: {declared})."
        steps = None
        if num_inference_steps not in (None, ""):
            try:
                steps = max(1, min(int(num_inference_steps), 100))
            except (TypeError, ValueError):
                return None, f"num_inference_steps must be a number, got {num_inference_steps!r}"
            # A speed profile's LoRA is distilled for its own step count, so the
            # profile's floor applies in place of the model's.
            profile_floor = (profiles.get(speed_profile) or {}).get("min_steps") if speed_profile else None
            floor = int(profile_floor or caps.get("min_steps") or 0)
            if floor and steps < floor:
                steps = floor

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
            style = str(style).strip().lower()
            if style not in (caps.get("prompt_styles") or []):
                from backend.services.video_render_limits import withheld_style
                return None, withheld_style(model_id, style) or (
                    f"Unknown style '{style}'. {entry['name']} offers: "
                    f"{', '.join(caps.get('prompt_styles') or [])}."
                )
            params["prompt_style"] = style
            if style == "none":
                params["enhance_prompt"] = False
        return params, None

    def execute(self, prompt: str, duration_frames: int = 49,
                num_inference_steps=None, wait_for_result: bool = False,
                model: Optional[str] = None, aspect_ratio: Optional[str] = None,
                duration_s=None, audio: bool = False, first_image: Optional[str] = None,
                last_image: Optional[str] = None, reference_images=None,
                reference_audio=None, speed_profile: Optional[str] = None,
                style: Optional[str] = None, reference_clips=None, **kwargs) -> ToolResult:
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
                "reference_clips": reference_clips, "reference_audio": reference_audio,
                "speed_profile": speed_profile, "style": style,
            }
            return run_tool_in_backend(
                self.name, {k: v for k, v in arguments.items() if v is not None},
                read_timeout=self.MAX_WAIT_S + 60,
            )
        wait_for_result = str(wait_for_result).lower() in ("1", "true", "yes")
        audio = str(audio).lower() in ("1", "true", "yes")
        reference_images = _ref_list(reference_images)
        reference_clips = _ref_list(reference_clips)
        reference_audio = _ref_list(reference_audio)

        params, err = self.resolve_request(
            prompt, model=model, aspect_ratio=aspect_ratio, duration_s=duration_s,
            duration_frames=duration_frames, num_inference_steps=num_inference_steps, audio=audio,
            first_image=first_image, last_image=last_image, reference_images=reference_images,
            reference_audio=reference_audio, speed_profile=speed_profile, style=style,
            reference_clips=reference_clips,
        )
        if err:
            return ToolResult(success=False, error=err)
        model_id = params["model"]
        duration_frames = params["duration_frames"]
        num_inference_steps = params.get("num_inference_steps")

        # Inputs resolve before the model preflight, which may start ComfyUI.
        mcp = is_mcp_caller(self)
        def _resolve(label, ref):
            found = _media_input(ref, mcp=mcp, label=label)
            return found.path, found.error

        first_path, err = _resolve("first_image", first_image)
        if not err:
            last_path, err = _resolve("last_image", last_image)
        resolved_refs = {"ref_images": [], "ref_videos": [], "ref_audios": []}
        for key, label, refs in (("ref_images", "reference image", reference_images),
                                 ("ref_videos", "reference clip", reference_clips),
                                 ("ref_audios", "reference audio", reference_audio)):
            for ref in refs:
                if err:
                    break
                path, err = _resolve(label, ref)
                resolved_refs[key].append({"path": path} if key == "ref_videos" else path)
        if err:
            return ToolResult(success=False, error=err)

        logger.info("VideoGeneratorTool: model=%s frames=%s steps=%s wait=%s prompt=%r",
                    model_id, duration_frames, num_inference_steps, wait_for_result, prompt[:100])
        try:
            from backend.services.batch_video_generator import get_batch_video_generator
            from backend.services.video_model_registry import prepare_video_model

            ready, preflight_err = prepare_video_model(model_id)
            if not ready:
                kind = failure_kind(preflight_err, RenderErrorKind.MODEL_NOT_INSTALLED)
                return ToolResult(success=False, error=failure_text(kind, preflight_err),
                                  metadata={"failure": describe_failure(kind, preflight_err)})

            generator = get_batch_video_generator()
            if not generator.service_available:
                return ToolResult(success=False, error="Video generation service not available")

            if any(resolved_refs.values()):
                # The render reads references from each item; the prompt names
                # them as <Picture N> / <Video N> / <Audio N> in wiring order.
                sent = prompt
                if params.get("enhance_prompt", True):
                    sent = _default_reference_prompt(prompt, resolved_refs, params)
                    params["enhance_prompt"] = False
                    from backend.services.batch_video_generator import _derive_display_name
                    params["metadata"].setdefault("display_name", _derive_display_name(prompt))
                status = generator.start_batch_from_prompts(prompts=[sent], item_metadata=resolved_refs,
                                                            **params)
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
                        f"Model: {model_id}",
                        f"Prompt: {prompt}",
                        f"Frames: {duration_frames} | Steps: {num_inference_steps}",
                        f"Open Video Gen: {studio_url}",
                        f"Jobs: {jobs_url}",
                        "The clip will appear in Studio → Video Gen when finished.",
                    ]),
                    metadata={
                        "prompt": prompt,
                        "model": model_id,
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
                    failure = batch_failure(status) or describe_failure(None, status.status)
                    return ToolResult(success=False, error=failure_text(failure["kind"], failure["message"]),
                                      metadata={"batch_id": batch_id, "failure": failure})
            else:
                return ToolResult(
                    success=True,
                    output="\n".join([
                        f"Video generation still running after {self.MAX_WAIT_S // 60} minutes "
                        f"(batch {batch_id}, model {model_id}).",
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
                failure = batch_failure(status) or describe_failure(
                    RenderErrorKind.OUTPUT_MISSING, "The batch finished without a video.")
                return ToolResult(success=False, error=failure_text(failure["kind"], failure["message"]),
                                  metadata={"batch_id": batch_id, "failure": failure})

            # video_path is batch-relative; the serving route accepts it verbatim.
            video_url = f"/api/batch-video/video/{batch_id}/{result.video_path}"
            gen_seconds = None
            if status.start_time and status.end_time:
                gen_seconds = (status.end_time - status.start_time).total_seconds()
            output_lines = [
                f"Video generated successfully" + (f" in {gen_seconds:.0f}s." if gen_seconds else "."),
                f"Model: {model_id}",
                f"Prompt: {prompt}",
                f"Frames: {duration_frames} | Steps: {num_inference_steps} | Batch: {batch_id}",
                f"Video: {video_url}",
                f"Open Video Gen: {studio_url}",
            ]
            metadata = {
                "prompt": prompt,
                "model": model_id,
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
    """Edit an EXISTING image from a natural-language instruction.

    Prefers Qwen-Image-Edit when installed, else FLUX.1 Kontext; with neither it
    refuses and names the pack to install. img2img runs only for a named image model.
    Use when the user SUPPLIES or ATTACHES an image and asks to add/remove/change
    something in it — e.g. 'put a cowboy hat on this character', 'make it night',
    'remove the sign'. Same canvas, same pose. For a brand-new scene of a face
    use generate_identity; for a brand-new picture with no reference use
    generate_image."""

    name = "edit_image"
    read_only = False
    destructive = False
    description = (
        "Edit an existing image using a natural-language instruction. Use this when "
        "the user has attached/uploaded an image (or names one) and asks to add, "
        "remove, or change something in it, e.g. 'put a cowboy hat on this character'. "
        "Preserves the original subject and only applies the requested edit. If the "
        "user did not attach an image, ask them to attach one (an MCP client passes image). Do NOT use this to make "
        "a brand-new image from scratch — use generate_image. For a new scene that "
        "keeps a face from an attached photo, use generate_identity in Guaardvark's chat "
        "(it asks for likeness consent; not offered over MCP). To extend the canvas use "
        "outpaint_image; to cut the subject out, remove_background." + _TOOL_JOB_NOTE
    )
    parameters = {
        "instruction": ToolParameter(
            name="instruction", type="string",
            description="The edit to perform, e.g. 'put a cowboy hat on this character', 'change the shirt to red'.",
            required=True,
        ),
        "image": ToolParameter(
            name="image", type="string",
            description=("The image to edit. In chat, omit it to use the image the user just attached; "
                         f"otherwise {_IMAGE_INPUT_FORMS}"),
            required=False, default="",
        ),
        "steps": ToolParameter(
            name="steps", type="int",
            description=("Diffusion steps (more = higher fidelity, slower). Default 28. A value is used as "
                         "given, except below a floor the editing model declares (Qwen-Image-Edit does), "
                         "where it is raised and the result says so."),
            required=False, default=28,
        ),
        "model": ToolParameter(
            name="model", type="string",
            description=(
                "Image model/backend. Default 'auto': Qwen-Image-Edit when installed, else FLUX.1 "
                "Kontext; with neither installed the call is refused and names the pack to install. "
                "In chat, 'auto' is replaced by the /imagemodel setting. 'qwen-image-edit' and "
                "'kontext' name a pack; any other downloaded image model runs a light img2img pass "
                "that keeps most of the picture."
            ),
            required=False, default="auto",
        ),
        "reference_image_2": ToolParameter(
            name="reference_image_2", type="string",
            description=("Optional second reference (another person or style), in the same forms as image. "
                         "Qwen-Image-Edit only: the call is refused when the edit would run on another "
                         "backend, or when the reference cannot be read."),
            required=False, default="",
        ),
        "reference_image_3": ToolParameter(
            name="reference_image_3", type="string",
            description="Optional third reference; same rules as reference_image_2.",
            required=False, default="",
        ),
        "wait_for_result": _wait_for_result_param(),
    }

    # The tool an edit runs for. inpaint_image borrows this class (``_edit_tool_for``)
    # under its own name, which picks the refusal wording and the backends allowed.
    for_tool = "edit_image"

    @staticmethod
    def _pick_edit_backend(model: str) -> Optional[str]:
        """'qwen', 'kontext' or 'img2img' for ``model``; None when it is 'auto' and
        neither editing pack is installed.

        img2img is only ever picked by naming an image model. At the strength an
        edit uses it returns a near copy of the photo, so it is not what 'auto'
        falls back to when a pack is missing.
        """
        m = (model or "auto").strip().lower()
        if m in _QWEN_EDIT_MODEL_IDS:
            return "qwen"
        if m in _KONTEXT_MODEL_IDS:
            return "kontext"
        if m != "auto":
            return "img2img"
        try:
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator
            gen = ComfyUIImageGenerator()
            if gen.qwen_edit_installed():
                return "qwen"
            if gen._kontext_installed():
                return "kontext"
        except Exception:
            pass
        return None

    def _edit_backend(self, model: str, references: int = 0):
        """The backend this edit runs on, or the ToolResult that refuses it.

        Refused: 'auto' with no editing pack installed, and reference images on
        a backend that edits one image. inpaint_image has no img2img form, so an
        image model named for it (chat passes the /imagemodel setting) means 'auto'.
        """
        backend = self._pick_edit_backend(model)
        if backend == "img2img" and self.for_tool != "edit_image":
            backend = self._pick_edit_backend("auto")
        if backend is None:
            from backend.services.image_editing_packs import missing_message
            return ToolResult(success=False, error=missing_message(self.for_tool))
        if references and backend != "qwen":
            runs_on = "FLUX.1 Kontext" if backend == "kontext" else f"img2img with '{model}'"
            return ToolResult(success=False, error=(
                f"Reference images are only used by Qwen-Image-Edit. This edit would run on {runs_on}, "
                "which edits one image and would ignore them. Call again without reference_image_2 and "
                "reference_image_3, or install Qwen-Image-Edit (Manage Image Models, Image editing) and "
                "use model 'auto' or 'qwen-image-edit'."))
        return backend

    @staticmethod
    def _effective_model(model: str) -> str:
        from backend.utils.settings_utils import get_chat_image_model
        return (model or "auto").strip() or get_chat_image_model()

    @staticmethod
    def _uses_kontext_backend(model: str) -> bool:
        return EditImageTool._pick_edit_backend(model) == "kontext"

    def _edit_via_img2img(
        self, *, src: str, instruction: str, model: str, output_path: str,
    ) -> ToolResult:
        from PIL import Image
        from backend.config import OUTPUT_DIR
        from backend.services.offline_image_generator import get_image_generator
        from backend.services.gpu_resource_policy import gpu_session
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
        gpu_wait = _chat_gpu_wait()
        session_kwargs = dict(evict_ollama=True, vram_estimate_mb=11000,
                              require_fit=True, cross_process=True)
        op_id = f"chat_edit_{uuid.uuid4().hex[:8]}"
        if gpu_wait and gpu_wait.get("wait_s"):
            from backend.services.gpu_resource_policy import gpu_session_when_free
            claim = gpu_session_when_free(
                JobKind.VIDEO_RENDER, op_id, wait_s=gpu_wait["wait_s"],
                on_wait=gpu_wait["on_wait"], should_stop=gpu_wait["should_stop"],
                **session_kwargs,
            )
        else:
            claim = gpu_session(JobKind.VIDEO_RENDER, op_id, on_busy="raise", **session_kwargs)
        try:
            with claim:
                result = generator.generate_image_from_image(
                    prompt=instruction,
                    init_image=init_image,
                    strength=0.35,
                    model=effective_model,
                    width=width,
                    height=height,
                    num_inference_steps=28,
                )
        except Exception as e:
            refusal = _gpu_refusal(e, gpu_wait)
            if refusal is None:
                raise
            return ToolResult(success=False, error=refusal)
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
        """The local file for ``image``, or None when it cannot be used."""
        return self._resolve_image_ref(image).path

    def _resolve_image_ref(self, image: str, label: str = "image"):
        """``image`` (data URI, served URL, resource URI or path) as a MediaRef.

        The chat engine injects the user's attached image as a disk path in the
        uploads folder; explicit URLs and paths go through the shared
        media-input rules (backend/utils/media_inputs.py), which are stricter
        for MCP clients and never download a remote URL.
        """
        from backend.utils.media_inputs import MediaRef, resolve_media_ref
        if not image:
            return MediaRef()
        # data URI or bare base64 blob: the caller sent the bytes, nothing is read from disk
        if image.startswith("data:") or (len(image) > 256 and "/" not in image[:64] and " " not in image[:64]):
            try:
                import base64
                from backend.config import OUTPUT_DIR
                edit_dir = os.path.join(OUTPUT_DIR, "edit_inputs")
                os.makedirs(edit_dir, exist_ok=True)
                raw = base64.b64decode(image.split(",", 1)[1] if image.startswith("data:") else image)
                p = os.path.join(edit_dir, f"edit_src_{uuid.uuid4().hex[:12]}.png")
                with open(p, "wb") as f:
                    f.write(raw)
                return MediaRef(path=p)
            except Exception as e:  # noqa: BLE001 — undecodable input is a plain refusal
                return MediaRef(error=f"{label} looked like base64 image data but did not decode: {e}")
        return resolve_media_ref(image, mcp=is_mcp_caller(self), label=label)

    def execute(self, instruction: str, image: str = "", steps: int = 28,
                model: str = "auto", reference_image_2: str = "",
                reference_image_3: str = "", wait_for_result: bool = False, **kwargs) -> ToolResult:
        if is_mcp_transport(self):
            # The edit renders on the GPU, which belongs to the backend process.
            return _forward_tool_job(self, {
                "instruction": instruction, "image": image, "steps": steps, "model": model,
                "reference_image_2": reference_image_2, "reference_image_3": reference_image_3,
            }, wait_for_result)
        inputs = self._edit_inputs(image, reference_image_2, reference_image_3)
        if isinstance(inputs, ToolResult):
            return inputs
        src, extra = inputs
        # Refused here, with the inputs, so an MCP client hears it at once and not as a failed job.
        backend = self._edit_backend(self._effective_model(model), len(extra))
        if isinstance(backend, ToolResult):
            return backend
        return _run_or_queue(self, lambda: self._edit(
            src, extra, instruction=instruction, steps=steps, model=model,
        ))

    def _edit_inputs(self, image: str, reference_image_2: str = "", reference_image_3: str = ""):
        """(source path, extra reference paths), or the ToolResult that refuses them."""
        found = self._resolve_image_ref(image)
        if not found.path:
            return ToolResult(
                success=False,
                error=found.error or "No image to edit. Ask the user to attach the image they want edited.",
            )
        extra = []
        for label, raw in (("reference_image_2", reference_image_2), ("reference_image_3", reference_image_3)):
            if not str(raw or "").strip():
                continue
            ref = self._resolve_image_ref(raw, label=label)
            if not ref.path:
                # A reference that was given and cannot be read changes what the edit means.
                return ToolResult(success=False, error=ref.error or f"{label} could not be read.")
            extra.append(ref.path)
        return found.path, extra

    def _edit(self, src: str, extra: list, *, instruction: str, steps, model: str) -> ToolResult:
        gpu_wait = None
        try:
            from backend.config import OUTPUT_DIR
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator

            effective_model = self._effective_model(model)
            backend = self._edit_backend(effective_model, len(extra))
            if isinstance(backend, ToolResult):
                return backend
            if backend != "img2img" and effective_model.lower() not in _QWEN_EDIT_MODEL_IDS | _KONTEXT_MODEL_IDS:
                effective_model = "auto"  # an image model named for a pack-only tool did not run
            output_dir = os.path.join(OUTPUT_DIR, "generated_images")
            os.makedirs(output_dir, exist_ok=True)
            filename = f"edit_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
            output_path = os.path.join(output_dir, filename)
            gen = ComfyUIImageGenerator()
            gpu_wait = _chat_gpu_wait()

            # No step count (None or 0) renders the model's own default; a given one
            # is used as given unless the model's registry entry declares a floor.
            if backend == "qwen":
                gen.edit_image_qwen(
                    image_paths=[src, *extra], instruction=instruction,
                    output_path=output_path, steps=steps, gpu_wait=gpu_wait,
                )
            elif backend == "kontext":
                gen.edit_image(
                    image_path=src, instruction=instruction,
                    output_path=output_path, steps=steps, gpu_wait=gpu_wait,
                )
            else:
                img2img_result = self._edit_via_img2img(
                    src=src, instruction=instruction,
                    model=effective_model, output_path=output_path,
                )
                if not img2img_result.success:
                    return img2img_result
                return ToolResult(
                    success=True,
                    output=img2img_result.output,
                    metadata={**(img2img_result.metadata or {}), "backend": "img2img"},
                )

            image_url = f"/api/outputs/generated_images/{filename}"
            return ToolResult(
                success=True,
                output="\n".join([
                    f"Image edited successfully ({backend}).",
                    f"Image URL: {image_url}",
                    f"Edit: {instruction}",
                    *_steps_lines(gen),
                ]),
                metadata={
                    "image_url": image_url,
                    "filename": filename,
                    "instruction": instruction,
                    "model": effective_model,
                    "backend": backend,
                    **_steps_metadata(gen),
                },
            )
        except Exception as e:
            refusal = _gpu_refusal(e, gpu_wait)
            if refusal:
                return ToolResult(success=False, error=refusal, metadata={"gpu_busy": True})
            logger.error(f"EditImageTool error: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Image edit failed: {str(e)}")


_GPU_WAIT_STATUS = "Waiting for the GPU: another render is running. This starts as soon as it finishes."
_GPU_STILL_BUSY = (
    "The GPU stayed busy with another render for {mins} minutes, so this did not run. "
    "Say \"try again\" when it is free."
)
_JOB_GPU_STILL_BUSY = (
    "The GPU stayed busy with other work for {mins} minutes, so this job did not run. Start it "
    "again when the GPU is free (inspect_gpu shows what holds it)."
)


def _image_vram_wait_s() -> float:
    """GUAARDVARK_IMAGE_VRAM_WAIT_S: how long image work waits for a busy GPU (default 600 s,
    the batch image wait)."""
    try:
        return max(0.0, float(os.environ.get("GUAARDVARK_IMAGE_VRAM_WAIT_S", "600")))
    except ValueError:
        return 600.0


def _job_gpu_wait() -> dict | None:
    """In a tool job, queue behind a busy GPU the way a batch image job does.

    No client holds the call open, so the job waits up to
    GUAARDVARK_IMAGE_VRAM_WAIT_S and get_generation_status shows it as queued
    meanwhile. Anywhere else: None.
    """
    if not _tool_jobs.in_job():
        return None
    return {"wait_s": _image_vram_wait_s(), "on_wait": _tool_jobs.note_gpu_wait,
            "should_stop": None, "still_busy": _JOB_GPU_STILL_BUSY}


def _chat_gpu_wait() -> dict | None:
    """On a chat turn, queue behind a busy GPU instead of refusing.

    Waits up to GUAARDVARK_IMAGE_VRAM_WAIT_S (the batch image wait, default
    600 s) with a status line in the chat, and gives up when the person presses
    Stop. A tool job waits too (``_job_gpu_wait``). Other callers (scripts, the
    Tools page) get None and keep the immediate answer.
    """
    from backend.services.agent_control_service import get_chat_emit_fn, get_chat_stop_check
    stop = get_chat_stop_check()
    if stop is None:
        return _job_gpu_wait()
    wait_s = _image_vram_wait_s()
    emit = get_chat_emit_fn()

    def on_wait(reason: str) -> None:
        logger.info("chat image job waiting for the GPU: %s", reason)
        if emit:
            emit("chat:thinking", {"status": _GPU_WAIT_STATUS})

    return {"wait_s": wait_s, "on_wait": on_wait, "should_stop": stop}


def _gpu_refusal(e: Exception, gpu_wait: dict | None) -> str | None:
    """Chat (or tool job) text for a GPU refusal, or None when ``e`` is not one."""
    from backend.services.gpu_resource_policy import GpuWaitStopped
    from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError
    if isinstance(e, GpuWaitStopped):
        return "Stopped before the GPU was free; nothing was rendered."
    if isinstance(e, GpuCapacityError):
        return None
    if isinstance(e, GpuBusyError):
        if gpu_wait and gpu_wait.get("wait_s"):
            text = gpu_wait.get("still_busy") or _GPU_STILL_BUSY
            return text.format(mins=max(1, round(gpu_wait["wait_s"] / 60)))
        return "GPU is busy with another render right now — try again in a moment."
    return None


# The least outpaint_image renders on FLUX.1 Kontext when a step count is given.
_KONTEXT_OUTPAINT_MIN_STEPS = 20

# `steps` on inpaint_image. The counts live on the editing models' registry
# entries (min_steps, default_steps), so none is repeated here.
_EDIT_STEPS_PARAM = (
    "Diffusion steps. Omit to render at the editing model's own count. A value is used as given, "
    "except below a floor the editing model declares (Qwen-Image-Edit does), where it is raised "
    "and the result says so."
)


def _steps_metadata(gen) -> dict:
    """The step count an edit generator rendered, and its notice when the ask was changed."""
    steps = getattr(gen, "last_steps", None)
    if steps is None:
        return {}
    return {"steps": steps, "steps_notice": getattr(gen, "last_steps_notice", None)}


def _steps_lines(gen) -> list:
    """``_steps_metadata`` as result lines."""
    meta = _steps_metadata(gen)
    if not meta:
        return []
    return [f"Steps: {meta['steps']}", *([meta["steps_notice"]] if meta["steps_notice"] else [])]


def _edit_tool_for(caller: BaseTool) -> "EditImageTool":
    """An EditImageTool that shares ``caller``'s context, so input rules see the same
    transport, and that edits on ``caller``'s behalf (``for_tool``)."""
    tool = EditImageTool()
    tool.set_context(dict(getattr(caller, "_context", None) or {}))
    tool.for_tool = caller.name
    return tool


def _chat_png_path(prefix: str) -> tuple[str, str]:
    from backend.config import OUTPUT_DIR
    output_dir = os.path.join(OUTPUT_DIR, "generated_images")
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    return os.path.join(output_dir, filename), filename


class RemoveBackgroundTool(BaseTool):
    """Cut the subject out of an attached photo (transparent PNG)."""

    name = "remove_background"
    read_only = False
    destructive = False
    description = (
        "Remove the background from an attached photo and return a transparent PNG. "
        "Use for product shots, stickers, and cut-outs. Does not invent a new scene: "
        "edit_image changes the picture, and generate_identity in Guaardvark's chat (not "
        "offered over MCP) makes a new scene with the same face. In chat the attached image is used "
        "when `image` is omitted; an MCP client passes `image`." + _TOOL_JOB_NOTE
    )
    parameters = {
        "image": ToolParameter(
            name="image", type="string",
            description=f"The photo. In chat, omit it to use the attached image; otherwise {_IMAGE_INPUT_FORMS}",
            required=False, default="",
        ),
        "wait_for_result": _wait_for_result_param(),
    }

    def execute(self, image: str = "", wait_for_result: bool = False, **kwargs) -> ToolResult:
        if is_mcp_transport(self):
            # The backend loads the cut-out model once and keeps it; this process does not load its own.
            return _forward_tool_job(self, {"image": image}, wait_for_result)
        found = _edit_tool_for(self)._resolve_image_ref(image)
        if not found.path:
            return ToolResult(success=False, error=found.error or "Attach the photo to cut out.")
        return _run_or_queue(self, lambda: self._cut_out(found.path))

    @staticmethod
    def _cut_out(src: str) -> ToolResult:
        from PIL import Image
        from backend.services.background_removal import (
            BackgroundRemovalNotInstalled, device_used, installed_model, remove_background,
        )
        from backend.services.image_editing_packs import pack_by_id
        try:
            model_id = installed_model()
            with Image.open(src) as im:
                out = remove_background(im, model_id)
            output_path, filename = _chat_png_path("nobg")
            out.save(output_path)
            image_url = f"/api/outputs/generated_images/{filename}"
            device = device_used(model_id)
            label = (pack_by_id(model_id) or {}).get("short") or model_id
            return ToolResult(
                success=True,
                output=f"Background removed ({label}, on the {device}).\nImage URL: {image_url}",
                metadata={"image_url": image_url, "filename": filename, "backend": model_id,
                          "device": device.lower()},
            )
        except BackgroundRemovalNotInstalled as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.error("remove_background failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"Background removal failed: {e}")


class InpaintImageTool(BaseTool):
    """Describe what to change or remove in an attached photo."""

    name = "inpaint_image"
    read_only = False
    destructive = False
    description = (
        "Change or remove something in an attached photo from a natural-language "
        "instruction ('remove the coffee cup', 'replace the sky with sunset'). "
        "Uses Qwen-Image-Edit when installed, else FLUX Kontext; with neither installed it "
        "refuses and names the pack to install. It runs the same edit as edit_image; "
        "either name works. For extending the canvas use outpaint_image. For a brand-new "
        "scene of a person's face use generate_identity in Guaardvark's chat (not offered "
        "over MCP)." + _TOOL_JOB_NOTE
    )
    parameters = {
        "instruction": ToolParameter(
            name="instruction", type="string",
            description="What to change or remove.",
            required=True,
        ),
        "image": ToolParameter(
            name="image", type="string",
            description=f"The photo. In chat, omit it to use the attached image; otherwise {_IMAGE_INPUT_FORMS}",
            required=False, default="",
        ),
        "steps": ToolParameter(
            name="steps", type="int",
            description=_EDIT_STEPS_PARAM,
            required=False, default=None,
        ),
        "wait_for_result": _wait_for_result_param(),
    }

    def execute(self, instruction: str, image: str = "", steps=None,
                wait_for_result: bool = False, **kwargs) -> ToolResult:
        if is_mcp_transport(self):
            return _forward_tool_job(self, {
                "instruction": instruction, "image": image, "steps": steps, "model": kwargs.get("model"),
            }, wait_for_result)
        model = kwargs.get("model") or "auto"
        edit = _edit_tool_for(self)
        inputs = edit._edit_inputs(image)
        if isinstance(inputs, ToolResult):
            return inputs
        src, extra = inputs
        backend = edit._edit_backend(edit._effective_model(model))
        if isinstance(backend, ToolResult):
            return backend
        return _run_or_queue(self, lambda: edit._edit(
            src, extra, instruction=instruction, steps=steps, model=model,
        ))


class OutpaintImageTool(BaseTool):
    """Extend the canvas and fill the new area."""

    name = "outpaint_image"
    read_only = False
    destructive = False
    description = (
        "Expand an attached photo in one or more directions and fill the new area "
        "so it matches the scene. Use when the user says extend, expand the canvas, "
        "or outpaint. Needs Qwen-Image-Edit to add canvas: with only FLUX Kontext "
        "installed the picture keeps its size and Kontext is asked to widen the view "
        "inside it. To change something in the picture use edit_image; to cut the subject "
        "out, remove_background." + _TOOL_JOB_NOTE
    )
    parameters = {
        "image": ToolParameter(
            name="image", type="string",
            description=f"The photo. In chat, omit it to use the attached image; otherwise {_IMAGE_INPUT_FORMS}",
            required=False, default="",
        ),
        "instruction": ToolParameter(
            name="instruction", type="string",
            description="Optional fill direction, e.g. 'continue the forest to the left'.",
            required=False, default="",
        ),
        "left": ToolParameter(name="left", type="int", description="Pixels to add on the left (multiples of 8).", required=False, default=0),
        "right": ToolParameter(name="right", type="int", description="Pixels to add on the right.", required=False, default=0),
        "top": ToolParameter(name="top", type="int", description="Pixels to add on the top.", required=False, default=0),
        "bottom": ToolParameter(name="bottom", type="int", description="Pixels to add on the bottom.", required=False, default=0),
        "steps": ToolParameter(
            name="steps", type="int", required=False, default=None,
            description=("Diffusion steps. Omit to render at the editing model's own count. Outpainting "
                         f"raises a lower value to at least {_KONTEXT_OUTPAINT_MIN_STEPS}."),
        ),
        "wait_for_result": _wait_for_result_param(),
    }

    def execute(self, image: str = "", instruction: str = "", left: int = 0, right: int = 0,
                top: int = 0, bottom: int = 0, steps=None, wait_for_result: bool = False,
                **kwargs) -> ToolResult:
        if is_mcp_transport(self):
            return _forward_tool_job(self, {
                "image": image, "instruction": instruction, "left": left, "right": right,
                "top": top, "bottom": bottom, "steps": steps,
            }, wait_for_result)
        found = _edit_tool_for(self)._resolve_image_ref(image)
        if not found.path:
            return ToolResult(success=False, error=found.error or "Attach the photo to extend.")
        src = found.path
        pad = {
            "left": max(0, int(left) or 0),
            "right": max(0, int(right) or 0),
            "top": max(0, int(top) or 0),
            "bottom": max(0, int(bottom) or 0),
            "feathering": 40,
        }
        if not any(pad[k] for k in ("left", "right", "top", "bottom")):
            # Default: grow 256 px on every side when the user didn't specify.
            pad.update({"left": 256, "right": 256, "top": 256, "bottom": 256})
        # The model is shown the original picture, not the padded canvas, so the
        # instruction describes a wider view of the scene rather than a border to fill.
        fill = (instruction or "").strip() or (
            "Show the same scene in a wider shot, continuing it past the edges with matching "
            "lighting, perspective and style. Keep everything already in the picture unchanged."
        )
        return _run_or_queue(self, lambda: self._outpaint(src, pad, fill, steps))

    @staticmethod
    def _outpaint(src: str, pad: dict, fill: str, steps) -> ToolResult:
        gpu_wait = None
        try:
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator
            gen = ComfyUIImageGenerator()
            output_path, filename = _chat_png_path("outpaint")
            gpu_wait = _chat_gpu_wait()
            if gen.qwen_edit_installed():
                gen.edit_image_qwen(
                    image_paths=[src], instruction=fill, output_path=output_path,
                    steps=steps, pad=pad, gpu_wait=gpu_wait,
                )
                backend = "qwen"
            elif gen._kontext_installed():
                # Kontext has no pad node in its graph; instruct it instead. A count
                # that is given is raised to at least 20; none renders the model's default.
                gen.edit_image(
                    image_path=src,
                    instruction=f"Outpaint: {fill}",
                    output_path=output_path,
                    steps=max(int(steps), _KONTEXT_OUTPAINT_MIN_STEPS) if steps else None,
                    gpu_wait=gpu_wait,
                )
                backend = "kontext"
            else:
                from backend.services.image_editing_packs import missing_message
                return ToolResult(success=False, error=missing_message("outpaint_image"))
            image_url = f"/api/outputs/generated_images/{filename}"
            done = ("Canvas extended (qwen)." if backend == "qwen" else
                    "Kontext widened the view inside the original frame; the canvas size is "
                    "unchanged. Install Qwen-Image-Edit to add canvas.")
            return ToolResult(
                success=True,
                output="\n".join([done, f"Image URL: {image_url}", *_steps_lines(gen)]),
                metadata={"image_url": image_url, "filename": filename, "backend": backend,
                          "pad": pad if backend == "qwen" else None, **_steps_metadata(gen)},
            )
        except Exception as e:
            refusal = _gpu_refusal(e, gpu_wait)
            if refusal:
                return ToolResult(success=False, error=refusal, metadata={"gpu_busy": True})
            logger.error("outpaint_image failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"Outpaint failed: {e}")


class GenerateIdentityTool(BaseTool):
    """New scene from a face photo (PuLID-FLUX). Consent is a stored record.

    The likeness gate is a ``.consent`` sidecar next to the resolved reference
    image (``backend.services.consent_records``), written only by the chat
    consent card or a UI upload. A ``consented`` argument from a caller is
    validated but is not proof: without the record the tool refuses with
    ``needs_consent`` in its metadata so the caller can obtain one.
    """

    name = "generate_identity"
    read_only = False
    destructive = False
    # The chat engine pauses on this tool and shows a consent card; on approval
    # it records consent for the reference image and then runs the tool.
    requires_approval = True
    consent_gate = True
    approval_prompt = (
        "Confirm you have the right to use this person's likeness: it is your "
        "own photo, or a Cast subject you uploaded. Guaardvark keeps a record "
        "next to the photo so it will not ask again for the same image."
    )
    description = (
        "Generate a brand-new image that keeps the face from an attached photo. "
        "Use when the user wants this person in a new scene, outfit, or era "
        "('this person as a 1940s detective'). Runs only for a likeness the user "
        "has confirmed they may use (their own photo or a Cast subject they "
        "uploaded); the chat asks for that confirmation. Not a face swap onto an "
        "existing poster; not an instruction edit of the same photo (use edit_image "
        "for that). Needs pulid-flux + flux-dev."
    )
    parameters = {
        "prompt": ToolParameter(
            name="prompt", type="string",
            description="The new scene, in prose. Do not repeat the person's name; identity comes from the photo.",
            required=True,
        ),
        "image": ToolParameter(
            name="image", type="string",
            description=f"Face reference. In chat, omit it to use the attached image; otherwise {_IMAGE_INPUT_FORMS}",
            required=False, default="",
        ),
        "consented": ToolParameter(
            name="consented", type="bool",
            description=(
                "Caller's statement that the user may use this likeness. Not proof on "
                "its own: the tool runs only when a consent record exists for the image."
            ),
            required=False, default=True,
        ),
        "width": ToolParameter(name="width", type="int", required=False, default=768),
        "height": ToolParameter(name="height", type="int", required=False, default=1024),
        "steps": ToolParameter(name="steps", type="int", required=False, default=20),
        # Likeness experiment switches; omitted = the generator's defaults.
        "weight": ToolParameter(
            name="weight", type="float", required=False,
            description="PuLID identity weight; default from the pulid-flux registry entry (1.0, measured 2026-09-19).",
        ),
        "start_at": ToolParameter(
            name="start_at", type="float", required=False,
            description="Fraction of the denoise at which identity starts applying; default from the registry (0.2: keeps the scene, then the face).",
        ),
        "end_at": ToolParameter(
            name="end_at", type="float", required=False,
            description="Fraction of the denoise at which identity stops applying (default 1.0).",
        ),
        "unet_dtype": ToolParameter(
            name="unet_dtype", type="string", required=False,
            description="FLUX UNET load dtype: fp8_e4m3fn, bf16 or default (default: the configured fp8).",
        ),
        "node_variant": ToolParameter(
            name="node_variant", type="string", required=False,
            description="PuLID apply node: pulid_flux (default) or pulid_classic.",
        ),
    }

    _PASSTHROUGH = ("weight", "start_at", "end_at", "unet_dtype", "node_variant")

    def execute(self, prompt: str, consented: bool = True, image: str = "",
                width: int = 768, height: int = 1024, steps: int = 20, **kwargs) -> ToolResult:
        from backend.services.consent_records import has_consent
        consented = str(consented).lower() in ("1", "true", "yes")
        if not consented:
            return ToolResult(
                success=False,
                error="generate_identity was called with consented=false. Only run it for a "
                      "likeness the user has the right to use (their photo or a Cast subject "
                      "they uploaded).",
            )
        found = _edit_tool_for(self)._resolve_image_ref(image)
        if not found.path:
            return ToolResult(success=False,
                              error=found.error or "Attach a face photo, then describe the new scene.")
        src = found.path
        if not has_consent(src):
            return ToolResult(
                success=False,
                error=(
                    "No consent record for this reference image. The user must confirm "
                    "they have the right to use this likeness (in chat, approve the consent "
                    "card; the record is stored next to the photo)."
                ),
                metadata={"needs_consent": True, "reference_image": src},
            )
        gpu_wait = None
        try:
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator
            output_path, filename = _chat_png_path("identity")
            overrides = {
                k: kwargs[k] for k in self._PASSTHROUGH
                if kwargs.get(k) is not None and kwargs.get(k) != ""
            }
            gpu_wait = _chat_gpu_wait()
            ComfyUIImageGenerator().generate_with_identity(
                image_path=src, prompt=prompt, output_path=output_path,
                width=int(width) or 768, height=int(height) or 1024,
                steps=int(steps) or 20, gpu_wait=gpu_wait, **overrides,
            )
            image_url = f"/api/outputs/generated_images/{filename}"
            return ToolResult(
                success=True,
                output=(
                    f"New image generated from the face reference (PuLID-FLUX).\n"
                    f"Image URL: {image_url}\nPrompt: {prompt}"
                ),
                metadata={
                    "image_url": image_url, "filename": filename,
                    "backend": "pulid-flux", "prompt": prompt,
                },
            )
        except Exception as e:
            refusal = _gpu_refusal(e, gpu_wait)
            if refusal:
                return ToolResult(success=False, error=refusal, metadata={"gpu_busy": True})
            logger.error("generate_identity failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"Identity generate failed: {e}")
