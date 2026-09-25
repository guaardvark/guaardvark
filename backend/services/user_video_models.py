"""User-added video models — same registry shape as the shipped catalog.

Hugging Face URLs parse to {hf_repo, src}; Install reuses the existing
hf_hub_download plan. Entries persist in data/user_video_models.json (gitignored)
and register into VIDEO_MODEL_REGISTRY at load so ComfyUI loaders and the
Video Gen selector see them. Shipped ids are never written here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from backend.services.user_model_families import (
    DuplicateUserModel,
    find_duplicate,
    inspect_hf_repo,
    match_families,
    parse_hf_url,
    sanitize_repo_src,
)

logger = logging.getLogger(__name__)

USER_MODEL_PREFIX = "user-"
DEFAULT_ADAPTER_STRENGTH = 0.7
WEIGHT_SUFFIXES = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin")
_CATALOG_LOCK = threading.Lock()
# Tests point this at a temp file. None → data/user_video_models.json.
_CATALOG_PATH_OVERRIDE = None
ROLES = ("lora", "generation", "encoder")

# Capability keys copied when the add is "a generation model like X".
_LIKE_GENERATION_KEYS = (
    "type",
    "local_subdir",
    "requires",
    "dimension_alignment",
    "max_pixel_area",
    "aspect_ratios",
    "min_steps",
    "default_steps",
    "native_fps",
    "max_frames",
    "modes",
    "cfg",
    "audio_out",
    "audio_in",
    "frame_rule",
    "min_vram_gb",
    "vram_mb",
)


def user_catalog_path() -> Path:
    if _CATALOG_PATH_OVERRIDE is not None:
        return Path(_CATALOG_PATH_OVERRIDE)
    root = os.environ.get("GUAARDVARK_ROOT", ".")
    return Path(root) / "data" / "user_video_models.json"


def is_user_model_id(model_id: str) -> bool:
    return str(model_id or "").startswith(USER_MODEL_PREFIX)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:48] or "model")


def unique_user_id(stem: str, taken: set | None = None) -> str:
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
    taken = set(taken or ()) | set(VIDEO_MODEL_REGISTRY)
    base = USER_MODEL_PREFIX + _slug(stem)
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def suggest_role_and_like(files: list, src: str | None = None, hf_repo: str = "") -> tuple[str | None, str | None]:
    """Guess LoRA vs encoder vs generation and a shipped family template from filenames."""
    matches = match_families(
        domain="video", files=files, src=src, hf_repo=hf_repo or "", has_model_index=False,
    )
    wired = [m for m in matches if m.get("wired")]
    if not wired:
        return None, None
    top = wired[0]
    return top.get("role"), top.get("like")


def preview_hf_url(url: str) -> dict:
    """Parse a paste and list the repo's weight files. Does not download."""
    inspected = inspect_hf_repo(url)
    matches = match_families(
        domain="video",
        files=inspected["files"],
        src=inspected.get("src"),
        hf_repo=inspected["hf_repo"],
        has_model_index=inspected.get("has_model_index") or False,
        pipeline_tag=inspected.get("pipeline_tag"),
        index_class=inspected.get("index_class"),
    )
    wired = [m for m in matches if m.get("wired")]
    unwired = [m for m in matches if not m.get("wired")]
    top = (unwired or wired or [None])[0]
    return {
        "hf_repo": inspected["hf_repo"],
        "revision": inspected.get("revision") or "main",
        "src": inspected.get("src"),
        "files": inspected["files"],
        "gated": inspected.get("gated") or False,
        "truncated": inspected.get("truncated") or False,
        "license": inspected.get("license"),
        "token_present": inspected.get("token_present") or False,
        "pipeline_tag": inspected.get("pipeline_tag"),
        "index_class": inspected.get("index_class"),
        "warnings": list(inspected.get("warnings") or []),
        "matches": matches,
        "suggested_role": (top or {}).get("role") if top and top.get("wired") else None,
        "suggested_like": (top or {}).get("like") if top and top.get("wired") else None,
        "unwired": unwired[0] if unwired else None,
    }


def _read_catalog() -> dict:
    path = user_catalog_path()
    if not path.exists():
        return {"models": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error("user video catalog unreadable (%s): %s", path, e)
        return {"models": {}}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return {"models": {}}
    return {"models": models}


def _write_catalog(catalog: dict) -> None:
    path = user_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _dst_for_generation_file(src: str, expert: str | None, like_entry: dict) -> str:
    name = Path(src).name
    like_type = like_entry.get("type")
    dsts = [f.get("dst") or "" for f in like_entry.get("files") or []]
    moe = any("HighNoise" in d for d in dsts) and any("LowNoise" in d for d in dsts)
    if moe or (like_type == "wan" and expert in ("high", "low")):
        tag = expert or (
            "high" if any(t in src.lower() for t in ("highnoise", "high_noise", "high_lighting", "_high_"))
            else "low" if any(t in src.lower() for t in ("lownoise", "low_noise", "low_lighting", "_low_"))
            else None
        )
        if tag == "high":
            return f"HighNoise/{name}"
        if tag == "low":
            return f"LowNoise/{name}"
    return name


def build_user_entry(
    *,
    role: str,
    like_id: str,
    hf_repo: str,
    files: list,
    name: str | None = None,
    model_id: str | None = None,
    description: str | None = None,
    revision: str = "main",
    known_files: list | None = None,
) -> tuple[str, dict]:
    """Build a registry entry cloned from a shipped template. Does not register.

    Roles: ``lora`` stacks on a generation model, ``generation`` is another UNET
    cloned from a shipped family, ``encoder`` swaps in for the text encoder a
    generation model ships with (its CLIPLoader file). An encoder entry keeps the
    companion's ``type: encoder`` and ``local_subdir`` so Install writes it next
    to the shipped one, and names what it stands in for in ``replaces``.
    """
    from backend.services.video_model_registry import (
        VIDEO_MODEL_REGISTRY,
        GENERATION_TYPES,
        LORA_STACK_TYPES,
        TEXT_ENCODER_SWAP_TYPES,
        shipped_encoder_for,
    )

    like = VIDEO_MODEL_REGISTRY.get(like_id)
    if not like:
        raise ValueError(f"Unknown template '{like_id}'. Pick a shipped model this file is like.")
    if role not in ROLES:
        raise ValueError(
            "Role must be 'lora' (adapter on a model), 'generation' (another UNET like a shipped one) "
            "or 'encoder' (a text encoder that replaces the one a model ships with)."
        )
    if not hf_repo or "/" not in hf_repo:
        raise ValueError("hf_repo must be org/repo.")
    if not files:
        raise ValueError("Pick at least one weight file. A whole-repo snapshot is refused.")

    specs = []
    total = 0
    known = None
    if known_files is not None:
        known = {f.get("src") if isinstance(f, dict) else f for f in known_files}
    for item in files:
        src = (item.get("src") if isinstance(item, dict) else None) or ""
        src = sanitize_repo_src(src)
        if known is not None and src not in known:
            raise ValueError(f"'{src}' is not a weight file in that repo.")
        expert = (item.get("expert") if isinstance(item, dict) else None) or None
        size = int((item.get("size") if isinstance(item, dict) else 0) or 0)
        total += size
        if role in ("lora", "encoder"):
            dst = Path(src).name
        else:
            dst = _dst_for_generation_file(src, expert, like)
        specs.append({"src": src, "dst": dst})

    stem = name or Path(specs[0]["src"]).stem
    mid = model_id or unique_user_id(stem)
    if not is_user_model_id(mid):
        raise ValueError(f"User model ids must start with '{USER_MODEL_PREFIX}'.")
    if mid in VIDEO_MODEL_REGISTRY and not is_user_model_id(mid):
        raise ValueError(f"'{mid}' is a shipped model and cannot be replaced.")

    size_gb = round(total / (1024 ** 3), 3) if total else 0.0
    if role == "lora":
        if like.get("type") not in LORA_STACK_TYPES:
            raise ValueError(
                f"{like.get('name') or like_id} does not stack LoRAs; "
                f"that works for {', '.join(LORA_STACK_TYPES)} models."
            )
        entry = {
            "name": name or stem,
            "description": description or f"User LoRA for {like.get('name') or like_id}.",
            "hf_repo": hf_repo,
            "revision": revision or "main",
            "local_subdir": "loras",
            "files": specs,
            "size_gb": size_gb,
            "vram_mb": 0,
            "type": "lora",
            "applies_to": [like_id],
            "strength": DEFAULT_ADAPTER_STRENGTH,
            "user": True,
        }
        return mid, entry

    if role == "encoder":
        if like.get("type") not in TEXT_ENCODER_SWAP_TYPES:
            raise ValueError(
                f"{like.get('name') or like_id} does not take a replacement text encoder yet; "
                f"that works for {', '.join(TEXT_ENCODER_SWAP_TYPES)} models."
            )
        shipped = shipped_encoder_for(like_id)
        shipped_entry = VIDEO_MODEL_REGISTRY.get(shipped) or {}
        if not shipped or not shipped_entry:
            raise ValueError(f"{like.get('name') or like_id} has no text-encoder companion to replace.")
        if len(specs) != 1:
            raise ValueError("A text encoder is one file. Pick the single .safetensors the CLIPLoader should read.")
        # Every generation model in the family that loads the same shipped encoder
        # can use the replacement, not only the one picked as the template.
        applies = sorted(
            mid_ for mid_, e in VIDEO_MODEL_REGISTRY.items()
            if e.get("type") == like.get("type") and shipped_encoder_for(mid_) == shipped
        ) or [like_id]
        entry = {
            "name": name or stem,
            "description": description or f"User text encoder for {like.get('name') or like_id}; replaces {shipped_entry.get('name') or shipped}.",
            "hf_repo": hf_repo,
            "revision": revision or "main",
            "local_subdir": shipped_entry.get("local_subdir") or "text_encoders",
            "files": specs,
            "size_gb": size_gb,
            "vram_mb": 0,
            "type": "encoder",
            "applies_to": applies,
            "replaces": shipped,
            "user": True,
        }
        return mid, entry

    if like.get("type") not in GENERATION_TYPES:
        raise ValueError(f"'{like_id}' is not a generation family a new UNET can clone.")
    moe_like = any("HighNoise" in (f.get("dst") or "") for f in like.get("files") or [])
    if moe_like:
        highs = [s for s in specs if "HighNoise" in s["dst"]]
        lows = [s for s in specs if "LowNoise" in s["dst"]]
        if len(highs) != 1 or len(lows) != 1:
            raise ValueError(
                f"{like.get('name')} is a two-expert model — pick one HighNoise file and one LowNoise file."
            )
    entry = {k: like[k] for k in _LIKE_GENERATION_KEYS if k in like}
    if like.get("hf_repo") == hf_repo and like.get("license"):
        entry["license"] = like["license"]
    entry.update({
        "name": name or stem,
        "description": description or f"User model like {like.get('name') or like_id}.",
        "hf_repo": hf_repo,
        "revision": revision or "main",
        "files": specs,
        "size_gb": size_gb or float(like.get("size_gb") or 0),
        "user": True,
        "like": like_id,
    })
    return mid, entry


def resolve_text_encoder(model_key: str, encoder_id: str | None) -> tuple[str | None, str | None]:
    """Filename the graph's CLIPLoader should read for a user-chosen encoder.

    Returns (None, None) when no swap was asked for, (filename, None) when the
    entry applies to this model and is installed, else (None, message).
    """
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, is_model_installed

    eid = (encoder_id or "").strip()
    if not eid:
        return None, None
    entry = VIDEO_MODEL_REGISTRY.get(eid) or {}
    label = entry.get("name") or eid
    if entry.get("type") != "encoder" or not entry.get("user"):
        return None, f"'{eid}' is not a text encoder you added."
    applies = entry.get("applies_to") or []
    if applies and model_key not in applies:
        return None, f"{label} does not replace the text encoder of this model."
    files = entry.get("files") or []
    filename = files[0].get("dst") if files else None
    if not filename:
        return None, f"{label} has no file."
    if not is_model_installed(eid):
        from backend.services.job_types import RenderErrorKind, RenderFailure
        return None, RenderFailure(
            RenderErrorKind.COMPANION_MISSING,
            f"{label} is not installed. Open Manage Video Models to download it.",
        )
    return filename, None


def add_user_model(**kwargs) -> tuple[str, dict, list]:
    """Validate, persist, and register. Returns (id, entry, verify problems)."""
    from backend.services.video_model_registry import register_video_model, VIDEO_MODEL_REGISTRY

    files = kwargs.get("files") or []
    hf_repo = kwargs.get("hf_repo") or ""
    revision = kwargs.get("revision") or "main"
    with _CATALOG_LOCK:
        catalog = _read_catalog()
        dup = find_duplicate(catalog, hf_repo=hf_repo, revision=revision, files=files)
        if dup:
            raise DuplicateUserModel(dup)
        mid, entry = build_user_entry(**kwargs)
        if mid in VIDEO_MODEL_REGISTRY and not is_user_model_id(mid):
            raise ValueError(f"'{mid}' is a shipped model and cannot be replaced.")
        problems = register_video_model(mid, entry, replace=is_user_model_id(mid) and mid in VIDEO_MODEL_REGISTRY)
        catalog["models"][mid] = entry
        _write_catalog(catalog)
    return mid, entry, problems


def remove_user_model(model_id: str, *, delete_files: bool = False) -> dict:
    """Drop a user catalog entry. Shipped ids raise. Returns what was removed."""
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, comfyui_models_dir

    if not is_user_model_id(model_id):
        raise ValueError("Only user-added models can be removed. The shipped catalog stays.")
    with _CATALOG_LOCK:
        catalog = _read_catalog()
        entry = catalog["models"].pop(model_id, None) or dict(VIDEO_MODEL_REGISTRY.get(model_id) or {})
        if not entry and model_id not in VIDEO_MODEL_REGISTRY:
            raise KeyError(f"Unknown user model '{model_id}'")
        VIDEO_MODEL_REGISTRY.pop(model_id, None)
        _write_catalog(catalog)
    removed_files = []
    skipped_shared = []
    if delete_files and entry:
        still_needed = set()
        for other in VIDEO_MODEL_REGISTRY.values():
            sub = other.get("local_subdir") or ""
            for spec in other.get("files") or []:
                still_needed.add((sub, spec.get("dst")))
        base = comfyui_models_dir() / (entry.get("local_subdir") or "")
        sub = entry.get("local_subdir") or ""
        for spec in entry.get("files") or []:
            dst = spec.get("dst")
            if (sub, dst) in still_needed:
                skipped_shared.append(dst)
                continue
            path = base / dst
            if path.exists() and path.is_file():
                path.unlink()
                removed_files.append(str(path))
    return {
        "id": model_id,
        "name": entry.get("name"),
        "deleted_files": removed_files,
        "kept_shared_files": skipped_shared,
    }


def load_user_catalog() -> list:
    """Register every persisted user entry. Safe at import; corrupt file is skipped."""
    from backend.services.video_model_registry import register_video_model, VIDEO_MODEL_REGISTRY

    catalog = _read_catalog()
    problems = []
    for mid, entry in (catalog.get("models") or {}).items():
        if not is_user_model_id(mid) or not isinstance(entry, dict):
            logger.error("skipping user video catalog id %r — not a user-* entry", mid)
            continue
        if mid in VIDEO_MODEL_REGISTRY and not VIDEO_MODEL_REGISTRY[mid].get("user"):
            logger.error("user catalog id %s collides with a shipped model — skipped", mid)
            continue
        try:
            problems.extend(register_video_model(mid, dict(entry), replace=True))
        except Exception as e:
            logger.error("user video model %s failed to register: %s", mid, e)
    return problems
