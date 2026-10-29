"""Declared families a person can add from a Hugging Face paste.

The Add Model dialogs match a repo against this table. A family that is not
wired is refused by name instead of being filed under SDXL or Wan. Sampling
floors and loaders stay on the shipped catalog entries named in ``like``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import unquote

# Weight names the inspector will list. GGUF is a ComfyUI UNET, not a
# diffusers snapshot, so it is in the union used for both domains.
WEIGHT_SUFFIXES = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin")
_HF_HOST = re.compile(r"^https?://(www\.)?(huggingface\.co|hf\.co)/", re.I)
_HF_LIST_CAP = 200
_ENCODER_HINTS = (
    "text_encoder", "text-encoder", "qwen3vl", "qwen3-vl", "umt5", "t5xxl",
    "_t5_", "gemma", "llava", "clip_l", "/clip/",
)
_LORA_HINTS = ("/loras/", "lora", "lycoris", "adapter")
_HF_TOKEN_ENV = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")

# Filename fragments that mark a two-expert Wan UNET pair.
_HIGH_TOKS = ("highnoise", "high_noise", "high_lighting", "_high_")
_LOW_TOKS = ("lownoise", "low_noise", "low_lighting", "_low_")


class DuplicateUserModel(ValueError):
    """Same hf_repo + revision + files already sits in the user catalog."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        super().__init__(
            f"That Hugging Face file is already in your catalog as '{model_id}'."
        )


# ── family table ────────────────────────────────────────────────────────────
# wired=False rows are detectable so Look up can refuse them by name.
# roles / layout / engine tell the dialogs what to ask for; like is the shipped
# catalog id whose sampling policy and (for Comfy) graph the add clones.
# comfy_graph names the ComfyUI text-to-image graph a Comfy family renders on
# (comfyui_image_generator._build_workflow); a Comfy family without one (the
# Chat editor) has no stills route. graph_models maps a UNET suffix to the
# shipped registry entry whose graph (and companions) a user file of that
# suffix renders on; files not listed render on `like`.
# weight_suffixes / refused_suffixes / refused_name_tokens say which weight
# files a Comfy family's loader reads; build_user_entry refuses the rest and
# Look up warns about them (weight_file_problem). A row without
# weight_suffixes takes every WEIGHT_SUFFIXES file.

# Apple MLX repacks carry the format in the name; ComfyUI has no loader for them.
_MLX_REFUSAL = {
    "mlx": "is an Apple MLX file, which ComfyUI cannot load. Pick an fp8 or int8 .safetensors instead.",
}

# Qwen-Image 2.1 spellings seen in repo ids, file names and base_model fields.
_QWEN21_TOKENS = (
    "qwen-image-2.1", "qwen_image_2.1", "qwenimage2.1", "qwenimage-2.1", "qwen-image2.1",
    "qwen-image-2-1", "qwen_image_2_1", "qwen-image-21", "qwen_image_21", "qwenimage21",
    "qwen image 2.1",
)

IMAGE_FAMILIES = (
    {
        "id": "zimage",
        "domain": "image",
        "wired": True,
        "label": "Z-Image",
        "engine": "offline",
        "like": "zimage-turbo",
        "roles": ("generation", "lora"),
        "layout": "snapshot",
        "single_file": False,
        "lora_engine": "offline",
        "tokens": ("z-image", "zimage"),
        "pipeline_tags": (),
        "index_classes": ("ZImagePipeline", "ZImageImg2ImgPipeline"),
    },
    {
        "id": "krea2",
        "domain": "image",
        "wired": True,
        "label": "Krea 2",
        "engine": "offline",
        "like": "krea2-turbo",
        "roles": ("generation",),
        "layout": "snapshot",
        "single_file": False,
        "lora_engine": None,
        "tokens": ("krea",),
        "pipeline_tags": (),
        "index_classes": ("Krea2Pipeline",),
    },
    {
        "id": "sdxl",
        "domain": "image",
        "wired": True,
        "label": "SDXL",
        "engine": "offline",
        "like": "sd-xl",
        "roles": ("generation", "lora"),
        "layout": "snapshot",
        "single_file": True,
        "lora_engine": "comfy",
        "tokens": ("sdxl", "sd-xl", "juggernaut", "pony", "illustrious", "noobai"),
        "pipeline_tags": ("stable-diffusion-xl",),
        "index_classes": (
            "StableDiffusionXLPipeline",
            "StableDiffusionXLImg2ImgPipeline",
            "StableDiffusionXLInpaintPipeline",
        ),
    },
    {
        "id": "sd",
        "domain": "image",
        "wired": True,
        "label": "SD 1.5",
        "engine": "offline",
        "like": "realistic-vision",
        "roles": ("generation",),
        "layout": "single_file",
        "single_file": True,
        "lora_engine": None,
        "tokens": ("sd15", "sd-15", "sd1.5", "sd-1.5", "stable-diffusion-v1"),
        "pipeline_tags": ("stable-diffusion",),
        "index_classes": (
            "StableDiffusionPipeline",
            "StableDiffusionImg2ImgPipeline",
            "StableDiffusionInpaintPipeline",
        ),
    },
    {
        "id": "flux",
        "domain": "image",
        "wired": True,
        "label": "FLUX.1",
        "engine": "comfy",
        "like": "flux-dev",
        "roles": ("generation", "lora", "encoder"),
        "layout": "comfy_files",
        "single_file": True,
        "lora_engine": "comfy",
        "comfy_graph": "flux",
        # A .gguf UNET renders on the schnell graph (UnetLoaderGGUF, fp8 T5);
        # any other file on the dev graph (`like`). Companions follow the graph.
        "graph_models": {".gguf": "flux-schnell"},
        # The text encoders a user file can stand in for: the two DualCLIPLoader
        # slots of both FLUX graphs, the architecture each reads
        # (text_encoder_check), and the shipped companion it replaces on each
        # graph. DualCLIPLoader reads .safetensors; a .gguf encoder would need
        # the GGUF CLIP loader, which these graphs do not use.
        "encoder_slots": {
            "t5": {
                "label": "T5-XXL",
                "arch": "t5-xxl",
                "replaces": {"flux-dev": "flux-t5-fp16", "flux-schnell": "flux-t5-fp8"},
            },
            "clip_l": {
                "label": "CLIP-L",
                "arch": "clip-l",
                "replaces": {"flux-dev": "flux-clip-l", "flux-schnell": "flux-clip-l"},
            },
        },
        "encoder_subdir": "clip",
        "encoder_suffixes": (".safetensors",),
        "refused_name_tokens": _MLX_REFUSAL,
        "tokens": ("flux", "flux.1", "flux1"),
        "pipeline_tags": ("flux",),
        "index_classes": ("FluxPipeline", "FluxImg2ImgPipeline", "FluxControlPipeline"),
    },
    {
        "id": "qwen-image-edit",
        "domain": "image",
        "wired": True,
        "label": "Qwen-Image-Edit",
        "engine": "comfy",
        "like": "qwen-image-edit",
        "roles": ("generation",),
        "layout": "comfy_files",
        "single_file": True,
        "lora_engine": None,
        "local_subdir": "diffusion_models",
        "refused_name_tokens": _MLX_REFUSAL,
        "tokens": ("qwen-image-edit", "qwenimage-edit", "qwen_image_edit"),
        "pipeline_tags": ("qwen-image-edit",),
        "index_classes": ("QwenImageEditPipeline", "QwenImageEditPlusPipeline"),
    },
    {
        "id": "qwen-image-21",
        "domain": "image",
        "wired": True,
        "label": "Qwen-Image 2.1",
        "engine": "comfy",
        "like": "qwen-image-21",
        "roles": ("generation",),
        "layout": "comfy_files",
        "single_file": True,
        "lora_engine": None,
        "local_subdir": "diffusion_models",
        "comfy_graph": "qwen-image-21",
        # The Qwen-Image 2.1 graph loads the UNET with UNETLoader, which reads
        # .safetensors; the bundled ComfyUI-GGUF loader does not list the 2.1
        # architecture (qwen_image21) either.
        "weight_suffixes": (".safetensors",),
        "refused_suffixes": {
            ".gguf": "is a GGUF; the Qwen-Image 2.1 graph here loads .safetensors with UNETLoader. "
                     "Pick a .safetensors file instead.",
        },
        "refused_name_tokens": _MLX_REFUSAL,
        "tokens": _QWEN21_TOKENS,
        "pipeline_tags": (),
        "index_classes": (),
    },
)

UNWIRED_FAMILIES = (
    {
        # Qwen-Image 1.0. Edit and 2.1 pastes are claimed first (_qwen_claim), so
        # this row only refuses the 1.0 checkpoints.
        "id": "qwen-image",
        "domain": "image",
        "wired": False,
        "label": "Qwen-Image 1.0",
        "tokens": ("qwen-image", "qwenimage", "qwen_image"),
        "pipeline_tags": ("qwen-image",),
        "index_classes": ("QwenImagePipeline",),
        "message": "Qwen-Image 1.0 text-to-image is not wired — Qwen-Image 2.1 is the supported "
                   "text-to-image family (Qwen-Image-Edit is the Chat editor).",
    },
    {
        "id": "sd3",
        "domain": "image",
        "wired": False,
        "label": "SD3 / SD3.5",
        "tokens": ("sd3.5", "sd3", "stable-diffusion-3"),
        "pipeline_tags": ("stable-diffusion-3",),
        "index_classes": ("StableDiffusion3Pipeline", "StableDiffusion3Img2ImgPipeline"),
        "message": "SD3 is not wired yet — this product cannot load that architecture.",
    },
    {
        "id": "hidream",
        "domain": "image",
        "wired": False,
        "label": "HiDream",
        "tokens": ("hidream",),
        "pipeline_tags": (),
        "index_classes": ("HiDreamImagePipeline",),
        "message": "HiDream is not wired yet — this product cannot load that architecture.",
    },
    {
        "id": "mochi",
        "domain": "video",
        "wired": False,
        "label": "Mochi",
        "tokens": ("mochi",),
        "pipeline_tags": (),
        "index_classes": ("MochiPipeline",),
        "message": "Mochi is not wired yet — this product cannot load that architecture.",
    },
    {
        "id": "ltx-video-0.9",
        "domain": "video",
        "wired": False,
        "label": "LTX-Video 0.9",
        # The 2B/13B LTXV checkpoints; LTX-2.x files are named ltx-2.3-… / ltx-2.5-….
        "tokens": ("ltxv", "ltx-video", "ltx_video"),
        "pipeline_tags": (),
        "index_classes": (
            "LTXPipeline", "LTXImageToVideoPipeline", "LTXConditionPipeline",
            "LTXLatentUpsamplePipeline",
        ),
        "message": (
            "LTX-Video 0.9 (the 2B/13B LTXV models) is not wired yet — "
            "the Studio runs LTX-2.3 and LTX-2.5."
        ),
    },
)

# Video add clones a shipped generation id (like). Tokens pick the type; extra
# tokens pick which shipped template, first match in order. moe likes need a
# High + Low file. Video tokens match whole words (_has_tok), not substrings.
VIDEO_FAMILIES = (
    {
        "id": "wan",
        "domain": "video",
        "wired": True,
        "label": "Wan",
        "roles": ("generation", "lora", "encoder"),
        # t2v / i2v are not Wan's: Hunyuan and CogVideoX name their files the same way.
        "tokens": ("wan",),
        "likes": {
            "generation": (
                {"like": "wan22-5b", "any": ("5b", "ti2v"), "moe": False},
                {"like": "wan22-14b-i2v", "any": ("i2v",), "moe": True},
                {"like": "wan22-14b", "any": ("14b", "wan"), "moe": True},
            ),
            "lora": (
                # A LoRA trained on the 5B cannot load on the 14B experts.
                {"like": "wan22-5b", "any": ("5b", "ti2v")},
                {"like": "wan22-14b-i2v", "any": ("i2v",)},
                {"like": "wan22-14b", "any": ("wan", "t2v", "14b")},
            ),
            "encoder": (
                {"like": "wan22-5b", "any": ("umt5", "wan")},
            ),
        },
    },
    {
        "id": "minimax",
        "domain": "video",
        "wired": True,
        "label": "MiniMax H3",
        "roles": ("generation", "lora", "encoder"),
        "tokens": ("minimax", "h3", "hailuo"),
        "likes": {
            "generation": ({"like": "minimax-h3-int8", "any": ("minimax", "h3", "hailuo"), "moe": False},),
            "lora": ({"like": "minimax-h3-int8", "any": ("minimax", "h3")},),
            "encoder": ({"like": "minimax-h3-int8", "any": ("qwen3vl", "qwen3-vl", "minimax", "h3")},),
        },
    },
    {
        "id": "ltx",
        "domain": "video",
        "wired": True,
        "label": "LTX",
        "roles": ("generation", "lora", "encoder"),
        "tokens": ("ltx", "ltx2", "gemma"),
        "likes": {
            "generation": (
                {"like": "ltx25-distilled-int8", "any": ("2.5", "ltx25", "ltx-2.5"), "moe": False},
                {"like": "ltx23-distilled-fp8", "any": ("ltx",), "moe": False},
            ),
            "lora": ({"like": "ltx25-distilled-int8", "any": ("2.5", "ltx25")}, {"like": "ltx23-distilled-fp8", "any": ("ltx",)}),
            "encoder": (
                {"like": "ltx25-distilled-int8", "any": ("gemma4", "gemma-4", "ltx-2.5", "ltx25")},
                {"like": "ltx23-distilled-fp8", "any": ("gemma", "ltx")},
            ),
        },
    },
    {
        "id": "hunyuan",
        "domain": "video",
        "wired": True,
        "label": "Hunyuan",
        "roles": ("generation", "lora", "encoder"),
        "tokens": ("hunyuan",),
        "likes": {
            "generation": (
                {"like": "hunyuan-i2v", "any": ("i2v",), "moe": False},
                {"like": "hunyuan-t2v", "any": ("hunyuan",), "moe": False},
            ),
            "lora": ({"like": "hunyuan-t2v", "any": ("hunyuan",)},),
            "encoder": ({"like": "hunyuan-t2v", "any": ("llava", "hunyuan")},),
        },
    },
    {
        "id": "cogvideox",
        "domain": "video",
        "wired": True,
        "label": "CogVideoX",
        "roles": ("generation",),
        "tokens": ("cogvideox", "cogvideo"),
        "likes": {
            "generation": (
                {"like": "cogvideox-5b-i2v", "any": ("i2v",), "moe": False},
                {"like": "cogvideox-5b", "any": ("cogvideo",), "moe": False},
            ),
        },
    },
)


def hf_token_present() -> bool:
    return any(os.environ.get(k) for k in _HF_TOKEN_ENV)


def image_generation_families() -> dict:
    """Shape consumed by user_image_models.build_user_entry."""
    out = {}
    for row in IMAGE_FAMILIES:
        if not row["wired"]:
            continue
        out[row["id"]] = {
            "label": row["label"],
            "like": row["like"],
            "single_file": bool(row["single_file"]),
            "engine": row["engine"],
            "lora": "lora" in row["roles"],
            "lora_engine": row.get("lora_engine"),
            "encoder": "encoder" in row["roles"],
            "layout": row["layout"],
            "local_subdir": row.get("local_subdir"),
            "comfy_graph": row.get("comfy_graph"),
            "graph_models": dict(row.get("graph_models") or {}),
        }
    return out


def encoder_slots(family_id: str) -> dict:
    """``{slot: {label, arch, replaces}}`` for a family that takes user text
    encoders; empty for the rest."""
    row = _image_family_row(family_id) or {}
    if "encoder" not in (row.get("roles") or ()):
        return {}
    return {k: dict(v) for k, v in (row.get("encoder_slots") or {}).items()}


def encoder_slot_for_arch(family_id: str, arch: str | None) -> str | None:
    """The slot of ``family_id`` that reads ``arch``, or None."""
    for slot, spec in encoder_slots(family_id).items():
        if spec.get("arch") == arch:
            return slot
    return None


def encoder_families_for_arch(arch: str | None) -> list:
    """Wired image families with a slot that reads ``arch``."""
    return [
        row["id"] for row in IMAGE_FAMILIES
        if row["wired"] and encoder_slot_for_arch(row["id"], arch)
    ]


def encoder_file_problem(family_id: str, filename: str | None) -> str | None:
    """Why a family's text-encoder loader cannot read ``filename``, or None."""
    row = _image_family_row(family_id) or {}
    name = Path(filename or "").name
    accepted = row.get("encoder_suffixes")
    if name and accepted and not name.lower().endswith(tuple(accepted)):
        return (
            f"{name} is not a text encoder {row.get('label', family_id)} loads here; "
            f"pick a {' or '.join(accepted)} file."
        )
    return None


def encoder_subdir(family_id: str) -> str:
    """The ComfyUI models/ folder a family's user text encoders install into."""
    return (_image_family_row(family_id) or {}).get("encoder_subdir") or "clip"


def comfy_graph_model(family_id: str, unet: str | None) -> str | None:
    """The shipped registry entry whose ComfyUI graph a user UNET of ``family_id``
    renders on, which is also the tag the graph builder dispatches on and the
    entry whose ``requires`` are the model's companions. None for families
    without a stills graph."""
    row = _image_family_row(family_id) or {}
    if not row.get("comfy_graph"):
        return None
    low = (unet or "").lower()
    for suffix, model_id in (row.get("graph_models") or {}).items():
        if low.endswith(suffix):
            return model_id
    return row.get("like")


def _image_family_row(family_id: str) -> dict | None:
    return next((r for r in IMAGE_FAMILIES if r["id"] == family_id), None)


def weight_file_problem(family_id: str, filename: str | None) -> str | None:
    """Why a Comfy family's loader cannot read ``filename``, or None when it can.

    Read from the family row: ``refused_name_tokens`` (a format named in the
    file, MLX), ``refused_suffixes`` (a suffix with its own reason), then
    ``weight_suffixes`` (anything else the loader does not take). Families that
    declare none accept every weight file.
    """
    row = _image_family_row(family_id) or {}
    name = Path(filename or "").name
    low = name.lower()
    if not low:
        return None
    for tok, why in (row.get("refused_name_tokens") or {}).items():
        if tok in low:
            return f"{name} {why}"
    for suffix, why in (row.get("refused_suffixes") or {}).items():
        if low.endswith(suffix):
            return f"{name} {why}"
    accepted = row.get("weight_suffixes")
    if accepted and not low.endswith(tuple(accepted)):
        return (
            f"{name} is not a file {row.get('label', family_id)} loads; "
            f"pick a {' or '.join(accepted)} file."
        )
    return None


def comfy_t2i_family(model_id: str | None) -> str | None:
    """The ComfyUI text-to-image family a model id renders on, or None.

    A user id carries its family as the prefix (``user-flux-…``,
    ``user-qwen-image-21-…``) and is answered from that alone; a user family
    without a stills graph (the Chat editor) is None. A shipped id or Comfy tag
    names the family by token (``flux-dev``, ``comfyui (flux-schnell)``,
    ``qwen-image-21``).
    """
    key = (model_id or "").strip().lower()
    if not key:
        return None
    graph_rows = [r for r in IMAGE_FAMILIES if r["wired"] and r.get("engine") == "comfy"]
    if key.startswith("user-"):
        for row in graph_rows:
            if key.startswith(f"user-{row['id']}-") or key == f"user-{row['id']}":
                return row["id"] if row.get("comfy_graph") else None
        return None
    for row in graph_rows:
        if row.get("comfy_graph") and (
            key == row["id"] or any(tok in key for tok in row["tokens"])
        ):
            return row["id"]
    return None


def image_lora_families() -> tuple:
    return tuple(
        row["id"] for row in IMAGE_FAMILIES
        if row["wired"] and "lora" in row["roles"]
    )


def family_choices(domain: str = "image") -> list:
    if domain != "image":
        return []
    return [
        {
            "id": row["id"],
            "label": row["label"],
            "like": row["like"],
            "single_file": bool(row["single_file"]),
            "lora": "lora" in row["roles"],
            "encoder": "encoder" in row["roles"],
            "encoder_slots": [
                {"id": slot, "label": spec["label"], "arch": spec["arch"]}
                for slot, spec in encoder_slots(row["id"]).items()
            ],
            "engine": row["engine"],
        }
        for row in IMAGE_FAMILIES
        if row["wired"]
    ]


def hf_inspect_url(hf_repo: str, revision: str = "main", src: str | None = None) -> str:
    """A huggingface.co URL inspect_hf_repo can parse from org/repo pieces."""
    repo = (hf_repo or "").strip()
    rev = (revision or "main").strip() or "main"
    if src:
        return f"https://huggingface.co/{repo}/blob/{rev}/{src}"
    if rev != "main":
        return f"https://huggingface.co/{repo}/tree/{rev}"
    return f"https://huggingface.co/{repo}"


def parse_hf_url(url: str) -> dict:
    """Turn a paste into {hf_repo, revision, src}.

    Accepts huggingface.co and hf.co, /blob|/resolve|/tree/{rev}/{path},
    and a bare org/repo. Query strings and trailing slashes are stripped.
    Dataset and Space URLs are refused — they are not model repos.
    ``refs/pr/N`` and ``refs/heads/…`` are kept as the revision.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Paste a Hugging Face URL or org/repo.")
    raw = raw.split("?")[0].split("#")[0].rstrip("/")
    # A URL copied from the browser escapes spaces and brackets in file names
    # (%20, %28); the Hub API and hf_hub_download take the plain name.
    raw = unquote(raw)
    raw = _HF_HOST.sub("", raw)
    if "://" in raw or raw.lower().startswith("www."):
        raise ValueError("Only Hugging Face URLs or org/repo ids are accepted.")
    parts = [p for p in raw.split("/") if p]
    if parts and parts[0].lower() in ("datasets", "spaces"):
        kind = "Space" if parts[0].lower() == "spaces" else "dataset"
        raise ValueError(
            f"That is a Hugging Face {kind}, not a model repo. "
            "Paste a model URL (huggingface.co/org/repo)."
        )
    if parts and parts[0].lower() == "models" and len(parts) >= 3:
        parts = parts[1:]
    if len(parts) < 2:
        raise ValueError("Need org/repo (for example Comfy-Org/MiniMax-H3).")
    repo = f"{parts[0]}/{parts[1]}"
    src = None
    revision = "main"
    if len(parts) >= 4 and parts[2] in ("blob", "resolve", "tree"):
        revision, src = _revision_and_src(parts[3:])
    elif len(parts) > 2:
        src = "/".join(parts[2:])
    if src:
        src = sanitize_repo_src(src)
    return {"hf_repo": repo, "revision": revision, "src": src}


def _revision_and_src(rest: list[str]) -> tuple[str, str | None]:
    """Split blob/resolve/tree remainder into revision and optional file path."""
    if not rest:
        return "main", None
    if rest[0] == "refs" and len(rest) >= 3:
        revision = "/".join(rest[:3])
        src = "/".join(rest[3:]) or None
        return revision, src
    revision = rest[0] or "main"
    src = "/".join(rest[1:]) or None
    return revision, src


def sanitize_repo_src(src: str) -> str:
    """A path inside the repo. Rejects empty, absolute, and parent-directory parts."""
    s = (src or "").replace("\\", "/").strip().lstrip("/")
    if not s:
        raise ValueError("File path must be a file inside the repo.")
    parts = [p for p in s.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        raise ValueError("File path must be a file inside the repo.")
    if s.startswith("/") or Path(s).is_absolute():
        raise ValueError("File path must be a file inside the repo.")
    return "/".join(parts)


def list_hf_weight_files(repo_id: str, revision: str = "main") -> tuple[list, bool, bool]:
    """Weight files (name + size). Caps at _HF_LIST_CAP. Returns (files, gated, truncated)."""
    from huggingface_hub import HfApi

    api = HfApi()
    info = api.repo_info(repo_id=repo_id, revision=revision, files_metadata=True)
    gated = bool(getattr(info, "gated", False))
    files = []
    truncated = False
    for sib in info.siblings or []:
        name = getattr(sib, "rfilename", None) or ""
        if not name.lower().endswith(WEIGHT_SUFFIXES):
            continue
        files.append({"src": name, "size": int(getattr(sib, "size", 0) or 0)})
        if len(files) >= _HF_LIST_CAP:
            truncated = True
            break
    return files, gated, truncated


def _card_field(info, *names):
    card = getattr(info, "card_data", None) or getattr(info, "cardData", None)
    for name in names:
        value = None
        if isinstance(card, dict):
            value = card.get(name)
        elif card is not None:
            value = getattr(card, name, None) or (card.get(name) if hasattr(card, "get") else None)
        if value:
            return value
    return None


def _license_from_info(info) -> str | None:
    lic = _card_field(info, "license", "licence") or getattr(info, "license", None)
    return str(lic) if lic else None


def _base_model_from_info(info) -> list:
    """The card's ``base_model`` (a repo id or a list of them), as a list."""
    value = _card_field(info, "base_model")
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [str(value)]


def inspect_hf_repo(url: str) -> dict:
    """Parse a paste and read the repo. Does not download weights."""
    from huggingface_hub import HfApi

    parsed = parse_hf_url(url)
    repo, revision = parsed["hf_repo"], parsed.get("revision") or "main"
    files, gated, truncated = list_hf_weight_files(repo, revision)
    src = parsed.get("src")
    if src and not any(f["src"] == src for f in files):
        files.insert(0, {"src": src, "size": 0})
    has_model_index = False
    pipeline_tag = None
    license_id = None
    base_model: list = []
    try:
        api = HfApi()
        info = api.model_info(repo, revision=revision)
        pipeline_tag = getattr(info, "pipeline_tag", None) or None
        license_id = _license_from_info(info)
        base_model = _base_model_from_info(info)
        gated = gated or bool(getattr(info, "gated", False))
    except Exception:  # noqa: BLE001 — listing already succeeded
        info = None
    try:
        all_files = HfApi().list_repo_files(repo, revision=revision)
        has_model_index = "model_index.json" in all_files
    except Exception:  # noqa: BLE001
        all_files = []
    index_class = _read_model_index_class(repo, revision) if has_model_index else None
    return {
        "hf_repo": repo,
        "revision": revision,
        "src": src,
        "files": files,
        "gated": gated,
        "truncated": truncated,
        "has_model_index": has_model_index,
        "pipeline_tag": pipeline_tag,
        "index_class": index_class,
        "license": license_id,
        "base_model": base_model,
        "token_present": hf_token_present(),
        "warnings": _inspect_warnings(files, has_model_index),
    }


def _class_stem(name: str | None) -> str:
    return (name or "").rsplit(".", 1)[-1].strip()


def _read_model_index_class(repo_id: str, revision: str = "main") -> str | None:
    """``_class_name`` from model_index.json. Small JSON only; never weights."""
    try:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(
            repo_id=repo_id, filename="model_index.json", revision=revision,
        )
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — listing already succeeded; class is a hint
        return None
    if not isinstance(data, dict):
        return None
    return _class_stem(data.get("_class_name")) or None


def _inspect_warnings(files: list, has_model_index: bool) -> list:
    warnings = []
    names = " ".join(f.get("src") or "" for f in files).lower()
    if any("-of-" in (f.get("src") or "").lower() for f in files) and not has_model_index:
        warnings.append(
            "This repo has sharded weights. Pick a merged .safetensors, or a diffusers repo root."
        )
    if any(n.endswith(".gguf") for n in names.split()) and has_model_index:
        warnings.append("Diffusers repo with GGUF files — pick the GGUF as a ComfyUI UNET, or snapshot the repo for an offline family.")
    return warnings


def _blob(files: list, src: str | None, hf_repo: str) -> str:
    names = [src] if src else [f.get("src") or "" for f in files]
    return " ".join(names + [hf_repo or ""]).lower()


def _base_models_text(base_model) -> str:
    if isinstance(base_model, (list, tuple)):
        return " ".join(str(b) for b in base_model if b).lower()
    return str(base_model or "").lower()


def _qwen_claim(blob: str, cls: str | None, base_model=None) -> str | None:
    """Which Qwen-Image family a paste names, in claim order: "edit", "21", or
    None (Qwen-Image 1.0, or no Qwen at all). The card's base_model counts for
    the 2.1 claim only, so a repack without a version in its names resolves;
    it stays out of the general match (a multi-base LoRA card names families
    the file is not)."""
    if "edit" in blob or (cls and "edit" in cls.lower()):
        return "edit"
    if any(tok in blob for tok in _QWEN21_TOKENS):
        return "21"
    if any(tok in _base_models_text(base_model) for tok in _QWEN21_TOKENS):
        return "21"
    return None


def _has_moe_pair(files: list, src: str | None) -> bool:
    names = [src] if src else [f.get("src") or "" for f in files]
    has_high = any(any(tok in n.lower() for tok in _HIGH_TOKS) for n in names)
    has_low = any(any(tok in n.lower() for tok in _LOW_TOKS) for n in names)
    return has_high and has_low


def _has_tok(blob: str, tok: str) -> bool:
    """tok as a word of its own: "i2v" is not in "ti2v", "wan" is in "wan2.2".

    Used for family and template tokens, which are short and collide. Role hints
    (lora, vae, t5xxl, ...) stay substrings: "AnimeLoRA" is still a LoRA.
    """
    return re.search(r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z])", blob) is not None


def _hit(blob: str, toks, whole_words: bool) -> bool:
    if whole_words:
        return any(_has_tok(blob, t) for t in toks)
    return any(t in blob for t in toks)


# Parts of a pipeline that ship beside the model in a repo, by folder or by name.
_COMPONENT_DIRS = (
    "text_encoder", "text_encoders", "tokenizer", "vae", "image_encoder", "scheduler",
    "clip", "clip_vision", "loras", "latent_upscale_models", "upscale_models", "embeddings",
)
_COMPONENT_NAME_HINTS = ("vae", "upscaler") + _ENCODER_HINTS


def _is_component(name: str) -> bool:
    low = name.lower()
    if any(part in _COMPONENT_DIRS for part in low.split("/")[:-1]):
        return True
    return any(k in low for k in _COMPONENT_NAME_HINTS + _LORA_HINTS)


def _role_names(files: list, src: str | None) -> list:
    """The filenames the role is read from.

    A pasted file is its own evidence. At a repo root it is the main weights
    only: one LoRA or T5 file beside the model must not make the whole repo a
    LoRA or an encoder.
    """
    if src:
        return [src]
    names = [f.get("src") or "" for f in files]
    main = [n for n in names if n and not _is_component(n)]
    return main or names


def _shipped_generation_match(hf_repo: str, src: str | None) -> dict | None:
    """The shipped generation entry this exact file belongs to, if any."""
    if not src or not hf_repo:
        return None
    try:
        from backend.services.video_model_registry import GENERATION_TYPES, VIDEO_MODEL_REGISTRY
    except Exception:
        return None
    for mid, entry in VIDEO_MODEL_REGISTRY.items():
        if mid.startswith("user-") or entry.get("type") not in GENERATION_TYPES:
            continue
        if (entry.get("hf_repo") or "") != hf_repo:
            continue
        srcs = [f.get("src") for f in (entry.get("files") or []) if isinstance(f, dict)]
        if src in srcs:
            return {
                "family": entry["type"],
                "label": entry.get("name") or mid,
                "role": "generation",
                "like": mid,
                "wired": True,
                "confidence": "high",
                "reason": "shipped file",
                "moe": len(srcs) == 2 and any("highnoise" in (x or "").lower() for x in srcs),
                "engine": "comfy",
                "shipped": True,
                "score": 10,
            }
    return None


def _guess_role(blob: str, domain: str) -> str:
    if any(k in blob for k in _LORA_HINTS):
        return "lora"
    if any(k in blob for k in _ENCODER_HINTS):
        return "encoder"
    return "generation"


def _pick_like(rules: tuple, blob: str) -> dict | None:
    for rule in rules:
        any_toks = rule.get("any") or ()
        if not any_toks or _hit(blob, any_toks, whole_words=True):
            return rule
    return None


def match_families(
    *,
    domain: str,
    files: list,
    src: str | None,
    hf_repo: str,
    has_model_index: bool = False,
    pipeline_tag: str | None = None,
    index_class: str | None = None,
    base_model=None,
) -> list[dict]:
    """Rank family matches. Unwired hits are returned alone so the UI refuses them.

    ``base_model`` is the card's base_model field; a Qwen-Image 2.1 repack whose
    file names carry no version still resolves through it.
    """
    blob = _blob(files, src, hf_repo)
    tag = (pipeline_tag or "").lower().replace("_", "-")
    cls = _class_stem(index_class)
    qwen_claim = _qwen_claim(blob, cls, base_model) if domain == "image" else None
    unwired = []
    for row in UNWIRED_FAMILIES:
        if row["domain"] != domain:
            continue
        hit = (
            _hit(blob, row["tokens"], whole_words=domain == "video")
            or tag in {t.lower() for t in row.get("pipeline_tags") or ()}
            or (cls and cls in (row.get("index_classes") or ()))
        )
        if row["id"] == "qwen-image" and qwen_claim:
            # Edit and 2.1 are wired families; only 1.0 is refused here.
            continue
        if hit:
            unwired.append({
                "family": row["id"],
                "label": row["label"],
                "role": "generation",
                "like": None,
                "wired": False,
                "confidence": "high",
                "reason": row["message"],
                "moe": False,
                "engine": None,
            })
    if unwired:
        return unwired

    if domain == "video":
        names = _role_names(files, src)
        blob = " ".join(names + [hf_repo or ""]).lower()
        role = _guess_role(blob, domain)
    else:
        # The role is read from the main weights: a LoRA or an encoder listed
        # beside an image model does not make the repo a LoRA.
        role = _guess_role(" ".join(_role_names(files, src) + [hf_repo or ""]).lower(), domain)
    matches = []

    if domain == "image":
        for row in IMAGE_FAMILIES:
            if not row["wired"]:
                continue
            score = 0
            reasons = []
            for tok in row["tokens"]:
                if tok in blob:
                    score += 3 if tok not in ("xl",) else 1
                    reasons.append(tok)
            if tag and tag in {t.lower() for t in row.get("pipeline_tags") or ()}:
                score += 4
                reasons.append(f"pipeline:{tag}")
            if cls and cls in (row.get("index_classes") or ()):
                score += 5
                reasons.append(f"class:{cls}")
            # "xl" in SDXL filenames is a weaker signal; score it only if no
            # stronger family already claimed the blob.
            if row["id"] == "sdxl" and "xl" in blob and score == 0:
                score += 1
                reasons.append("xl")
            if row["id"] == "sd" and has_model_index and score == 0 and role == "generation":
                # Fallback for a diffusers repo with no family tokens.
                score += 1
                reasons.append("diffusers")
            if row["id"] == "zimage" and role == "lora" and score == 0:
                score += 1
                reasons.append("lora-default")
            if role == "encoder" and score == 0 and row.get("encoder_slots"):
                # Encoder files rarely name the model they serve; a family that
                # takes one is the candidate, and the header check decides.
                score += 1
                reasons.append("encoder")
            if row["id"] == "qwen-image-21" and qwen_claim == "21" and score == 0:
                score += 3
                reasons.append("base_model")
            if role not in row["roles"]:
                continue
            if score <= 0:
                continue
            matches.append({
                "family": row["id"],
                "label": row["label"],
                "role": role if role in row["roles"] else "generation",
                "like": row["like"],
                "wired": True,
                "confidence": "high" if score >= 3 else "low",
                "reason": ", ".join(reasons) or row["id"],
                "moe": False,
                "engine": row["engine"],
                "score": score,
            })
        if not matches and role == "generation" and has_model_index:
            sd = next(r for r in IMAGE_FAMILIES if r["id"] == "sd")
            matches.append({
                "family": "sd",
                "label": sd["label"],
                "role": "generation",
                "like": sd["like"],
                "wired": True,
                "confidence": "low",
                "reason": "diffusers repo",
                "moe": False,
                "engine": sd["engine"],
                "score": 1,
            })
        if not matches and role == "lora":
            zi = next(r for r in IMAGE_FAMILIES if r["id"] == "zimage")
            matches.append({
                "family": "zimage",
                "label": zi["label"],
                "role": "lora",
                "like": zi["like"],
                "wired": True,
                "confidence": "low",
                "reason": "LoRA default",
                "moe": False,
                "engine": zi["engine"],
                "score": 1,
            })

    elif domain == "video":
        moe_pair = _has_moe_pair(files, src)
        shipped = _shipped_generation_match(hf_repo, src)
        if shipped:
            matches.append(shipped)
        for row in VIDEO_FAMILIES:
            if role not in row["roles"]:
                continue
            type_hit = _hit(blob, row["tokens"], whole_words=True)
            rules = (row.get("likes") or {}).get(role) or ()
            picked = _pick_like(rules, blob) if (type_hit or rules) else None
            if not picked:
                continue
            # Skip types that did not actually appear in the paste unless the
            # like-rule's tokens did (encoder hints often name the type).
            if not type_hit and not _hit(blob, picked.get("any") or (), whole_words=True):
                continue
            if shipped and picked["like"] == shipped["like"]:
                continue
            score = 3 if type_hit else 2
            matches.append({
                "family": row["id"],
                "label": row["label"],
                "role": role,
                "like": picked["like"],
                "wired": True,
                "confidence": "high" if type_hit else "low",
                "reason": row["id"],
                "moe": bool(picked.get("moe")) or (moe_pair and row["id"] == "wan"),
                "engine": "comfy",
                "score": score + (1 if moe_pair and picked.get("moe") else 0),
            })
        if moe_pair and not any(m["family"] == "wan" for m in matches):
            matches.append({
                "family": "wan",
                "label": "Wan",
                "role": "generation",
                "like": "wan22-14b-i2v" if _has_tok(blob, "i2v") else "wan22-14b",
                "wired": True,
                "confidence": "high",
                "reason": "HighNoise + LowNoise pair",
                "moe": True,
                "engine": "comfy",
                "score": 4,
            })

        if not matches and cls and has_model_index:
            # A diffusers pipeline no wired family claimed: refuse it by name
            # rather than leave the person to pick a template that cannot load it.
            return [{
                "family": None,
                "label": cls,
                "role": "generation",
                "like": None,
                "wired": False,
                "confidence": "high",
                "reason": f"{cls} is not a video architecture this product can load yet.",
                "moe": False,
                "engine": None,
            }]

    matches.sort(key=lambda m: -int(m.get("score") or 0))
    for m in matches:
        m.pop("score", None)
    return matches


def find_duplicate(catalog: dict, *, hf_repo: str, revision: str, files: list, kind: str | None = None) -> str | None:
    """Existing user-catalog id with the same repo + revision + file srcs, or None."""
    models = (catalog or {}).get("models") or {}
    wanted = tuple(sorted(
        (item.get("src") if isinstance(item, dict) else "") or ""
        for item in (files or [])
        if (item.get("src") if isinstance(item, dict) else item)
    ))
    rev = revision or "main"
    for mid, entry in models.items():
        if not isinstance(entry, dict):
            continue
        if (entry.get("hf_repo") or "") != hf_repo:
            continue
        if (entry.get("revision") or "main") != rev:
            continue
        have = tuple(sorted(f.get("src") or "" for f in (entry.get("files") or []) if f.get("src")))
        if kind == "snapshot" and entry.get("kind") == "snapshot":
            return mid
        if wanted and have == wanted:
            return mid
        if not wanted and not have and entry.get("kind") == "snapshot":
            return mid
    return None
