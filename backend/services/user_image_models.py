"""User-added image models — Hugging Face repos and files beside the shipped catalog.

The shipped stills catalog lives on OfflineImageGenerator.available_models. A
person can add three kinds of thing from a pasted Hugging Face URL:

  generation / snapshot     a diffusers repo (has model_index.json), cloned as a
                            new model of a shipped family: Z-Image, Krea 2, SDXL, SD
  generation / single_file  one .safetensors checkpoint, loaded with
                            from_single_file; SD and SDXL only, the families
                            diffusers can rebuild from a merged checkpoint.
                            Install also fetches the base pipeline's configs
                            and tokenizer files beside it (base_config/), so
                            loading never asks the hub for them
  lora                      an adapter file the offline pipeline stacks on a model
                            of its family (Z-Image today; SDXL and FLUX LoRAs run
                            on the ComfyUI path, which this catalog does not drive)
  encoder                   a text encoder that stands in for one a ComfyUI family
                            ships with, in one of the slots its family row declares
                            (FLUX: T5-XXL or CLIP-L). It installs into its own
                            folder under ComfyUI/models/clip/user/, so it never
                            lands on a shipped file's name, and is picked per render

Entries persist in data/user_image_models.json (gitignored) and register into the
generator at construction so the picker, the downloader and the loader see them.
Ids are ``user-<family>-<slug>``: the family sits in the id because the sampling
defaults (stills_defaults.model_family) and the page (batchImageSettings.modelFamily)
read the family off the id string, and the slug is scrubbed of the other families'
markers so that read cannot be fooled.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path

from backend.services import text_encoder_check as te_check
from backend.services.user_model_families import (
    DuplicateUserModel,
    comfy_graph_model,
    comfy_t2i_family,
    encoder_families_for_arch,
    encoder_file_problem,
    encoder_slot_for_arch,
    encoder_slots,
    encoder_subdir,
    family_choices as _table_family_choices,
    find_duplicate,
    image_generation_families,
    image_lora_families,
    inspect_hf_repo,
    match_families,
    sanitize_repo_src,
    weight_file_problem,
)

logger = logging.getLogger(__name__)

USER_MODEL_PREFIX = "user-"
DEFAULT_LORA_STRENGTH = 0.8
_CATALOG_LOCK = threading.Lock()
_CATALOG_PATH_OVERRIDE = None
WEIGHT_SUFFIXES = (".safetensors", ".ckpt", ".pt", ".bin", ".gguf")

# Families a user model can join. Source of truth is user_model_families.
GENERATION_FAMILIES = image_generation_families()
# Z-Image stacks offline; SDXL and FLUX LoRAs land in ComfyUI/models/loras.
LORA_FAMILIES = image_lora_families()
# Markers each family's id detection keys on; scrubbed from a slug that belongs
# to a different family.
_FAMILY_MARKERS = {
    "zimage": ("zimage", "z-image"),
    "krea2": ("krea",),
    "sdxl": ("xl",),
    "flux": ("flux",),
    "sana": ("sana",),
    "qwen-image-21": ("qwen-image-21",),
}
ROLES = ("generation", "lora", "encoder")
# Look up reads the headers of a repo's .safetensors files (a few KB each, in
# parallel) when there are at most this many; a bigger repo is checked on the
# one file picked, when it is added.
HEADER_PREVIEW_MAX_FILES = 8
# User encoders install under <encoder_subdir>/user/<id>/.
USER_ENCODER_DIR = "user"


def user_catalog_path() -> Path:
    if _CATALOG_PATH_OVERRIDE is not None:
        return Path(_CATALOG_PATH_OVERRIDE)
    root = os.environ.get("GUAARDVARK_ROOT", ".")
    return Path(root) / "data" / "user_image_models.json"


def is_user_model_id(model_id: str) -> bool:
    return str(model_id or "").startswith(USER_MODEL_PREFIX)


def family_safe_slug(text: str, family: str) -> str:
    """A slug that no other family's id detection will claim."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower())
    for fam, markers in _FAMILY_MARKERS.items():
        if fam == family:
            continue
        for marker in markers:
            s = s.replace(marker, "-")
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:40] or "model"


def user_model_id(name: str, family: str, taken=None) -> str:
    base = f"{USER_MODEL_PREFIX}{family}-{family_safe_slug(name, family)}"
    taken = set(taken or ())
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def suggest_role_and_family(files: list, src: str | None, hf_repo: str, has_model_index: bool):
    """Guess LoRA vs generation and the family from names alone."""
    matches = match_families(
        domain="image", files=files, src=src, hf_repo=hf_repo or "",
        has_model_index=has_model_index,
    )
    top = next((m for m in matches if m.get("wired")), None)
    if not top:
        return "generation", "sd"
    return top.get("role") or "generation", top.get("family") or "sd"


def preview_hf_url(url: str, *, read_headers: bool = True) -> dict:
    """Parse a paste and list the repo's weight files; says whether it is a diffusers repo.

    With ``read_headers``, each .safetensors file listed (the pasted one alone
    when a file was pasted) is tagged with the architecture its header shows,
    and the suggested role follows the file the person is most likely adding:
    a header beats a file name.
    """
    inspected = inspect_hf_repo(url)
    matches = match_families(
        domain="image",
        files=inspected["files"],
        src=inspected.get("src"),
        hf_repo=inspected["hf_repo"],
        has_model_index=inspected.get("has_model_index") or False,
        pipeline_tag=inspected.get("pipeline_tag"),
        index_class=inspected.get("index_class"),
        base_model=inspected.get("base_model"),
    )
    wired = [m for m in matches if m.get("wired")]
    unwired = [m for m in matches if not m.get("wired")]
    top = (unwired or wired or [None])[0]
    warnings = list(inspected.get("warnings") or [])
    files = [dict(f) for f in inspected["files"]]
    if top and top.get("wired") and top.get("role") == "generation":
        warnings.extend(_annotate_weight_problems(files, top.get("family") or ""))
    suggested_role = (top or {}).get("role") if top and top.get("wired") else None
    suggested_family = (top or {}).get("family") if top and top.get("wired") else None
    suggested_slot = None
    if read_headers and not unwired and not inspected.get("has_model_index"):
        _annotate_archs(inspected["hf_repo"], inspected.get("revision") or "main", files, inspected.get("src"))
        picked = _likely_file(files, inspected.get("src"))
        arch = (picked or {}).get("arch")
        if te_check.is_text_encoder(arch):
            families = encoder_families_for_arch(arch)
            if families:
                suggested_role, suggested_family = "encoder", families[0]
                suggested_slot = encoder_slot_for_arch(families[0], arch)
            else:
                suggested_role = suggested_family = None
                warnings.append(_no_slot_message(picked["src"], arch))
        elif arch == "diffusion" and suggested_role == "encoder":
            suggested_role = "generation"
        elif arch == "lora" and suggested_role != "lora":
            suggested_role = "lora"
            if suggested_family and suggested_family not in LORA_FAMILIES:
                suggested_family = None
        elif suggested_role == "encoder":
            suggested_slot = _slot_by_name(suggested_family, (picked or {}).get("src"))
    return {
        "hf_repo": inspected["hf_repo"],
        "revision": inspected.get("revision") or "main",
        "src": inspected.get("src"),
        "files": files,
        "gated": inspected.get("gated") or False,
        "truncated": inspected.get("truncated") or False,
        "has_model_index": inspected.get("has_model_index") or False,
        "license": inspected.get("license"),
        "base_model": list(inspected.get("base_model") or []),
        "token_present": inspected.get("token_present") or False,
        "pipeline_tag": inspected.get("pipeline_tag"),
        "index_class": inspected.get("index_class"),
        "warnings": warnings,
        "matches": matches,
        "suggested_role": suggested_role,
        "suggested_family": suggested_family,
        "suggested_slot": suggested_slot,
        "unwired": unwired[0] if unwired else None,
        "families": family_choices(),
    }


def _annotate_archs(hf_repo: str, revision: str, files: list, src: str | None) -> None:
    """Tag files with ``arch`` and ``arch_label`` read from their headers on the Hub."""
    if src:
        targets = [f for f in files if f.get("src") == src]
    else:
        targets = [f for f in files if (f.get("src") or "").lower().endswith(".safetensors")]
        if len(targets) > HEADER_PREVIEW_MAX_FILES:
            return
    targets = [f for f in targets if (f.get("src") or "").lower().endswith(".safetensors")]
    if not targets:
        return
    from concurrent.futures import ThreadPoolExecutor

    def _read(f):
        tensors = te_check.hf_tensors(hf_repo, f["src"], revision)
        return f, (te_check.classify(tensors) if tensors else None)

    with ThreadPoolExecutor(max_workers=min(HEADER_PREVIEW_MAX_FILES, len(targets))) as pool:
        for f, arch in pool.map(_read, targets):
            if arch:
                f["arch"] = arch
                f["arch_label"] = te_check.label(arch)


def _likely_file(files: list, src: str | None) -> dict | None:
    """The file the person is adding: the pasted one, or the only one listed."""
    if src:
        return next((f for f in files if f.get("src") == src), None)
    return files[0] if len(files) == 1 else None


def _slot_by_name(family: str | None, src: str | None) -> str | None:
    """A slot guessed from a file name when its header was not read."""
    low = Path(src or "").name.lower()
    slots = encoder_slots(family or "")
    if "t5" in low and "umt5" not in low and "t5" in slots:
        return "t5"
    if "clip" in low and "clip_l" in slots:
        return "clip_l"
    return None


def _no_slot_message(src: str, arch: str) -> str:
    takes = []
    for fam_id, spec in GENERATION_FAMILIES.items():
        labels = [s["label"] for s in encoder_slots(fam_id).values()]
        if labels:
            takes.append(f"{spec['label']} takes a {' or a '.join(labels)}")
    tail = f" ({'; '.join(takes)})" if takes else ""
    return (
        f"{Path(src).name} is a {te_check.label(arch)} text encoder. No image model here "
        f"takes a replacement one of that kind yet{tail}."
    )


def header_problem(*, role: str, family: str, slot: str | None, hf_repo: str,
                   src: str | None, revision: str = "main") -> str | None:
    """Why the picked file cannot be added in this role, read from its header
    on the Hub; None when it fits, or when the header could not be read (the
    render checks the downloaded file again)."""
    if not src or not src.lower().endswith(".safetensors"):
        return None
    spec = GENERATION_FAMILIES.get(family) or {}
    if role == "generation" and spec.get("engine") != "comfy":
        return None
    tensors = te_check.hf_tensors(hf_repo, src, revision)
    if not tensors:
        return None
    arch = te_check.classify(tensors)
    name = Path(src).name
    if role == "encoder":
        slot_spec = encoder_slots(family).get(slot or "") or {}
        if not slot_spec:
            return None
        return te_check.mismatch(arch, slot_spec["arch"], src, slot_spec["label"])
    if te_check.is_text_encoder(arch):
        families = encoder_families_for_arch(arch)
        where = (
            f" Add it with the role Text encoder ({GENERATION_FAMILIES[families[0]]['label']})."
            if families else ""
        )
        what = {"generation": "model", "lora": "LoRA"}.get(role, role)
        return f"{name} is a {te_check.label(arch)} text encoder, not a {spec.get('label', family)} {what}.{where}"
    if role == "lora" and arch == "diffusion":
        return f"{name} is a full diffusion model, not a LoRA. Add it as a new generation model instead."
    if role == "generation" and arch == "lora":
        return f"{name} is a LoRA, not a model. Add it as a LoRA on a model instead."
    return None


def _annotate_weight_problems(files: list, family: str) -> list:
    """Mark each listed file the family's loader cannot read (``problem``) and
    return one warning per distinct reason, naming the files it covers."""
    by_reason: dict[str, list[str]] = {}
    for f in files:
        problem = weight_file_problem(family, f.get("src"))
        if not problem:
            continue
        f["problem"] = problem
        name = Path(f.get("src") or "").name
        reason = problem[len(name):].strip() if problem.startswith(name) else problem
        by_reason.setdefault(reason, []).append(name)
    return [f"{', '.join(names)} {reason}" for reason, names in by_reason.items()]


def family_choices() -> list:
    return _table_family_choices("image")


def _read_catalog() -> dict:
    path = user_catalog_path()
    if not path.exists():
        return {"models": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error("user image catalog unreadable (%s): %s", path, e)
        return {"models": {}}
    models = data.get("models") if isinstance(data, dict) else None
    return {"models": models if isinstance(models, dict) else {}}


def _write_catalog(catalog: dict) -> None:
    path = user_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def build_user_entry(
    *,
    role: str,
    family: str,
    hf_repo: str,
    files: list,
    has_model_index: bool = False,
    name: str | None = None,
    description: str | None = None,
    revision: str = "main",
    taken=None,
    known_files: list | None = None,
    slot: str | None = None,
) -> tuple[str, dict]:
    """Validate the pieces and shape a catalog entry. Does not persist or register.

    ``slot`` is the shipped text encoder an ``encoder`` entry stands in for
    (a key of the family row's ``encoder_slots``).
    """
    if role not in ROLES:
        raise ValueError(
            "Role must be 'generation' (a model to pick), 'lora' (an adapter on a model) "
            "or 'encoder' (a text encoder that stands in for the one a model ships with)."
        )
    if family not in GENERATION_FAMILIES:
        raise ValueError(
            f"Family must be one of {', '.join(GENERATION_FAMILIES)}."
        )
    if not hf_repo or "/" not in hf_repo:
        raise ValueError("hf_repo must be org/repo.")
    specs = []
    total = 0
    known = None
    known_sizes = {}
    if known_files is not None:
        known = set()
        for f in known_files:
            if isinstance(f, dict):
                s = f.get("src")
                if s:
                    known.add(s)
                    known_sizes[s] = int(f.get("size") or 0)
            elif f:
                known.add(f)
    for item in files or []:
        src = (item.get("src") if isinstance(item, dict) else None) or ""
        if not src:
            continue
        src = sanitize_repo_src(src)
        if known is not None and src not in known:
            raise ValueError(f"'{src}' is not a weight file in that repo.")
        size = int((item.get("size") if isinstance(item, dict) else 0) or 0) or known_sizes.get(src, 0)
        specs.append({"src": src, "dst": Path(src).name, "size": size})
        total += size
    size_gb = round(total / (1024 ** 3), 3) if total else 0.0
    spec = GENERATION_FAMILIES[family]
    engine = spec.get("engine") or "offline"
    local_subdir = None

    if role == "lora":
        if family not in LORA_FAMILIES:
            raise ValueError(
                f"{spec['label']} LoRAs are not stacked; "
                f"that works for {', '.join(GENERATION_FAMILIES[f]['label'] for f in LORA_FAMILIES)} today."
            )
        if len(specs) != 1:
            raise ValueError("A LoRA is one file. Pick the single .safetensors to stack.")
        kind = "lora"
        if spec.get("lora_engine") == "comfy":
            engine = "comfy"
            local_subdir = "loras"
    elif role == "encoder":
        slots = encoder_slots(family)
        if not slots:
            takers = [GENERATION_FAMILIES[f]["label"] for f in GENERATION_FAMILIES if encoder_slots(f)]
            raise ValueError(
                f"{spec['label']} does not take a replacement text encoder"
                + (f"; that works for {', '.join(takers)} today." if takers else ".")
            )
        if slot not in slots:
            raise ValueError(
                "Pick which text encoder it stands in for: "
                f"{' or '.join(s['label'] for s in slots.values())}."
            )
        if len(specs) != 1:
            raise ValueError("A text encoder is one file. Pick the single .safetensors the loader should read.")
        problem = encoder_file_problem(family, specs[0]["src"])
        if problem:
            raise ValueError(problem)
        kind = "encoder"
        engine = "comfy"
        local_subdir = encoder_subdir(family)
    elif spec.get("engine") == "comfy":
        if len(specs) != 1:
            raise ValueError(
                f"A {spec['label']} stills model is one UNET file (.safetensors or .gguf). Pick exactly one."
            )
        problem = weight_file_problem(family, specs[0]["src"])
        if problem:
            raise ValueError(problem)
        kind = "comfy_files"
        local_subdir = spec.get("local_subdir") or "unet"
    elif has_model_index:
        if specs:
            # A diffusers repo is fetched whole; a file list would only confuse the
            # snapshot downloader, which already skips redundant root checkpoints.
            specs = []
        kind = "snapshot"
    else:
        if not spec["single_file"]:
            raise ValueError(
                f"{spec['label']} models load from a diffusers repo (one with model_index.json); "
                "this repo has none. Paste the repo root of a diffusers build."
            )
        if len(specs) != 1:
            raise ValueError("A single-file checkpoint is one .safetensors. Pick exactly one.")
        kind = "single_file"

    stem = name or (Path(specs[0]["src"]).stem if specs else hf_repo.split("/")[-1])
    mid = user_model_id(stem, family, taken)
    shared_with = None
    if role == "encoder":
        shipped = _shipped_encoder_file(
            hf_repo, specs[0]["src"], prefer=(encoder_slots(family)[slot].get("replaces") or {}).values(),
        )
        if shipped:
            # The file is already one of the shipped companions: point at it
            # instead of fetching a second copy, and never delete it on Remove.
            shared_with, local_subdir, specs[0]["dst"] = shipped
        else:
            specs[0]["dst"] = f"{USER_ENCODER_DIR}/{mid}/{Path(specs[0]['src']).name}"
    kind_word = {"lora": "LoRA", "encoder": "text encoder"}.get(role, "model")
    entry = {
        "name": name or stem,
        "description": description or f"User {kind_word} · {spec['label']} · {hf_repo}",
        "role": role,
        "family": family,
        "kind": kind,
        "like": spec["like"],
        "hf_repo": hf_repo,
        "revision": revision or "main",
        "files": specs,
        "size_gb": size_gb,
        "applies_to": [family] if role in ("lora", "encoder") else [],
        "strength": DEFAULT_LORA_STRENGTH if role == "lora" else None,
        "user": True,
        "engine": engine,
        "local_subdir": local_subdir,
    }
    if role == "encoder":
        slot_spec = encoder_slots(family)[slot]
        entry.update({
            "slot": slot,
            "arch": slot_spec["arch"],
            "replaces": dict(slot_spec.get("replaces") or {}),
            "shared_with": shared_with,
        })
    return mid, entry


def _shipped_encoder_file(hf_repo: str, src: str, prefer=()) -> tuple | None:
    """``(companion id, local_subdir, dst)`` when ``hf_repo``/``src`` is a file a
    shipped text-encoder companion already declares. One file can be declared by
    several companions under different names (FLUX's fp8 T5 is also CogVideoX's);
    an id in ``prefer`` (the companions the slot replaces) wins."""
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
    found = []
    for eid, entry in VIDEO_MODEL_REGISTRY.items():
        if entry.get("user") or entry.get("type") != "encoder" or entry.get("hf_repo") != hf_repo:
            continue
        for f in entry.get("files") or []:
            if f.get("src") == src and f.get("dst"):
                found.append((eid, entry.get("local_subdir") or "clip", f["dst"]))
    preferred = set(prefer or ())
    return next((hit for hit in found if hit[0] in preferred), found[0] if found else None)


# ── registration into the offline generator ─────────────────────────────────

def user_sentinel(model_id: str) -> str:
    return f"user:{model_id}"


def register_entry(generator, model_id: str, entry: dict) -> None:
    """Teach an OfflineImageGenerator about one entry (idempotent)."""
    for attr in ("family_overrides", "user_files", "user_entries"):
        if not isinstance(getattr(generator, attr, None), dict):
            setattr(generator, attr, {})
    if not isinstance(getattr(generator, "hidden_models", None), set):
        generator.hidden_models = set(getattr(generator, "hidden_models", ()) or ())
    family = entry.get("family")
    generator.user_entries[model_id] = dict(entry)
    generator.family_overrides[model_id] = family
    if entry.get("kind") == "snapshot":
        generator.available_models[model_id] = entry["hf_repo"]
        generator.family_overrides[entry["hf_repo"]] = family
    else:
        sentinel = user_sentinel(model_id)
        generator.available_models[model_id] = sentinel
        generator.family_overrides[sentinel] = family
        generator.user_files[sentinel] = {
            "dir": model_id,
            "hf_repo": entry["hf_repo"],
            "revision": entry.get("revision") or "main",
            "files": list(entry.get("files") or []),
            "kind": entry.get("kind"),
            "family": family,
            "engine": entry.get("engine") or "offline",
            "local_subdir": entry.get("local_subdir"),
            "shared_with": entry.get("shared_with"),
        }
    if entry.get("role") in ("lora", "encoder"):
        generator.hidden_models.add(model_id)
    generator.model_meta[model_id] = {
        "label": entry.get("name") or model_id,
        "description": entry.get("description") or "",
        "recommended": False,
        "order": 50 + len(generator.user_entries),
        "engine": entry.get("engine") or GENERATION_FAMILIES.get(family, {}).get("engine") or "offline",
        "user": True,
        "role": entry.get("role"),
        "family": family,
        "kind": entry.get("kind"),
        "size_gb": entry.get("size_gb") or 0.0,
        "applies_to": list(entry.get("applies_to") or []),
        "strength": entry.get("strength"),
        "slot": entry.get("slot"),
    }


def unregister_entry(generator, model_id: str) -> None:
    sentinel = user_sentinel(model_id)
    for attr in ("available_models", "model_meta", "user_entries", "family_overrides"):
        d = getattr(generator, attr, None)
        if isinstance(d, dict):
            d.pop(model_id, None)
    if isinstance(getattr(generator, "family_overrides", None), dict):
        generator.family_overrides.pop(sentinel, None)
    if isinstance(getattr(generator, "user_files", None), dict):
        generator.user_files.pop(sentinel, None)
    if isinstance(getattr(generator, "hidden_models", None), set):
        generator.hidden_models.discard(model_id)


def load_user_catalog(generator) -> int:
    """Register every persisted entry. Safe at construction; a bad entry is skipped."""
    catalog = _read_catalog()
    n = 0
    for mid, entry in (catalog.get("models") or {}).items():
        if not is_user_model_id(mid) or not isinstance(entry, dict):
            logger.error("skipping user image catalog id %r — not a user-* entry", mid)
            continue
        try:
            register_entry(generator, mid, entry)
            n += 1
        except Exception as e:  # noqa: BLE001 — one bad row must not hide the rest
            logger.error("user image model %s failed to register: %s", mid, e)
    return n


def add_user_model(generator, **kwargs) -> tuple[str, dict]:
    """Validate, persist and register. Returns (id, entry)."""
    with _CATALOG_LOCK:
        catalog = _read_catalog()
        dup = find_duplicate(
            catalog,
            hf_repo=kwargs.get("hf_repo") or "",
            revision=kwargs.get("revision") or "main",
            files=kwargs.get("files") or [],
            kind="snapshot" if kwargs.get("has_model_index") else None,
        )
        if dup:
            raise DuplicateUserModel(dup)
        taken = set(catalog["models"]) | set(getattr(generator, "available_models", {}) or {})
        mid, entry = build_user_entry(taken=taken, **kwargs)
        register_entry(generator, mid, entry)
        catalog["models"][mid] = entry
        _write_catalog(catalog)
    return mid, entry


def remove_user_model(generator, model_id: str, *, delete_files: bool = False) -> dict:
    """Drop a user entry. Shipped keys raise. Optionally delete its files."""
    if not is_user_model_id(model_id):
        raise ValueError("Only user-added models can be removed. The shipped catalog stays.")
    with _CATALOG_LOCK:
        catalog = _read_catalog()
        entry = catalog["models"].pop(model_id, None) or dict(
            (getattr(generator, "user_entries", {}) or {}).get(model_id) or {}
        )
        if not entry:
            raise KeyError(f"Unknown user model '{model_id}'")
        unregister_entry(generator, model_id)
        _write_catalog(catalog)
    removed = []
    if delete_files and entry.get("shared_with"):
        # A shipped companion's file; the shipped models still load it.
        pass
    elif delete_files:
        if (entry.get("engine") == "comfy" or entry.get("local_subdir")) and entry.get("files"):
            from backend.services.video_model_registry import comfyui_models_dir
            base = comfyui_models_dir() / (entry.get("local_subdir") or "unet")
            for spec in entry.get("files") or []:
                path = base / spec.get("dst")
                if path.exists() and path.is_file():
                    path.unlink()
                    removed.append(str(path))
                if entry.get("role") == "encoder" and path.parent.name == model_id:
                    try:
                        path.parent.rmdir()
                    except OSError:
                        pass
        else:
            base = Path(generator.models_dir)
            target = base / (entry["hf_repo"].replace("/", "--") if entry.get("kind") == "snapshot" else model_id)
            # A snapshot directory can be shared with a shipped model of the same repo.
            shared = entry.get("kind") == "snapshot" and any(
                v == entry.get("hf_repo") for k, v in (generator.available_models or {}).items() if k != model_id
            )
            if target.is_dir() and not shared:
                import shutil
                shutil.rmtree(target, ignore_errors=True)
                removed.append(str(target))
    return {"id": model_id, "name": entry.get("name"), "deleted_paths": removed}


# ── generation-time helpers ─────────────────────────────────────────────────

def _user_dest_dir(generator, uf: dict) -> Path:
    if (uf.get("engine") == "comfy" or uf.get("local_subdir")) and uf.get("kind") in ("lora", "comfy_files", "encoder"):
        from backend.services.video_model_registry import comfyui_models_dir
        return comfyui_models_dir() / (uf.get("local_subdir") or "unet")
    return Path(generator.models_dir) / uf["dir"]


def user_download_dir(generator, model_id: str) -> Path | None:
    """Dest directory for a user catalog key (Comfy unet/loras or models_dir/<id>)."""
    files = getattr(generator, "user_files", None) or {}
    uf = files.get(model_id)
    if uf is None:
        uf = files.get(user_sentinel(model_id))
    if uf is None:
        av = (getattr(generator, "available_models", None) or {}).get(model_id)
        uf = files.get(av or "")
    if not uf:
        return None
    return _user_dest_dir(generator, uf)


def user_files_present(generator, sentinel: str) -> bool:
    uf = (getattr(generator, "user_files", {}) or {}).get(sentinel)
    if not uf:
        return False
    return _entry_files_present(generator, uf)


def _entry_files_present(generator, uf: dict) -> bool:
    base = _user_dest_dir(generator, uf)
    for spec in uf.get("files") or []:
        p = base / spec["dst"]
        if not p.is_file() or p.stat().st_size == 0:
            return False
    return bool(uf.get("files"))


# ── companions of a user ComfyUI generation model ───────────────────────────
# A user UNET renders on its family's shipped graph, with the encoder and VAE
# that shipped entry (`like`) requires. Install fetches them beside the UNET,
# and the model is ready only once they are on disk too.

def _user_file_entry_for(generator, key: str) -> dict | None:
    files = getattr(generator, "user_files", None) or {}
    uf = files.get(key)
    if uf is None:
        uf = files.get(user_sentinel(key))
    if uf is None:
        av = (getattr(generator, "available_models", None) or {}).get(key)
        uf = files.get(av or "")
    return uf


def _graph_model_of(uf: dict | None) -> str | None:
    """The shipped registry entry whose graph a user ComfyUI generation model
    renders on (user_model_families.comfy_graph_model); None for LoRAs,
    snapshots and families without a stills graph."""
    if not uf or uf.get("engine") != "comfy" or uf.get("kind") != "comfy_files":
        return None
    files = uf.get("files") or []
    unet = (files[0].get("dst") if files else None) or None
    return comfy_graph_model(uf.get("family") or "", unet)


def companion_ids(generator, key: str) -> list:
    """Registry ids a user ComfyUI generation model renders with: the ``requires``
    of the shipped entry whose graph it renders on (a FLUX .gguf renders on
    flux-schnell and needs its fp8 T5, a .safetensors on flux-dev's fp16 T5)."""
    graph_model = _graph_model_of(_user_file_entry_for(generator, key))
    if not graph_model:
        return []
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
    return list((VIDEO_MODEL_REGISTRY.get(graph_model) or {}).get("requires") or [])


def missing_companions(generator, key: str) -> list:
    from backend.services.video_model_registry import is_model_installed
    return [eid for eid in companion_ids(generator, key) if not is_model_installed(eid)]


def companion_dirs(generator, key: str) -> list:
    """Directories the companions download into (for progress watching)."""
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, resolve_entry_dir
    return [resolve_entry_dir(VIDEO_MODEL_REGISTRY[eid]) for eid in companion_ids(generator, key)]


def missing_companion_size_gb(generator, key: str) -> float:
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
    return float(sum(
        float(VIDEO_MODEL_REGISTRY[eid].get("size_gb") or 0) for eid in missing_companions(generator, key)
    ))


def user_missing_files(generator, key: str) -> list:
    """``"<id>:<file>"`` for the user model's own absent files and its missing companions'."""
    uf = _user_file_entry_for(generator, key)
    if not uf:
        return []
    model_id = key if is_user_model_id(key) else (uf.get("dir") or key)
    missing = []
    base = _user_dest_dir(generator, uf)
    for spec in uf.get("files") or []:
        p = base / spec["dst"]
        if not p.is_file() or p.stat().st_size == 0:
            missing.append(f"{model_id}:{spec['dst']}")
    from backend.services.video_model_registry import missing_plan_files
    for eid in missing_companions(generator, key):
        missing.extend(missing_plan_files(eid))
    return missing


def user_model_ready(generator, sentinel: str) -> bool:
    """The user's files are on disk and, for a ComfyUI generation model, so are
    the companions its graph loads; a single-file checkpoint also needs the base
    config it is rebuilt with."""
    return (
        user_files_present(generator, sentinel)
        and not missing_companions(generator, sentinel)
        and single_file_config_present(generator, sentinel)
    )


# ── base config of a single-file checkpoint ─────────────────────────────────
# from_single_file rebuilds an SD / SDXL pipeline from the checkpoint plus the
# base pipeline's configs and tokenizer files (model_index.json,
# unet/config.json, tokenizer/vocab.json, ...). Left to itself, diffusers fetches
# those from the hub the first time the model loads. Install fetches them into
# base_config/ beside the checkpoint instead, and the loader reads only that.

SINGLE_FILE_CONFIG_DIR = "base_config"
# What diffusers fetches for this itself (loaders/single_file.py,
# _download_diffusers_model_config_from_hub): configs and tokenizers, no weights.
_SINGLE_FILE_CONFIG_PATTERNS = ["**/*.json", "*.json", "*.txt", "**/*.txt", "**/*.model"]


def single_file_config_dir(generator, key: str) -> Path | None:
    """The base config folder of a single-file model, or None for other kinds."""
    uf = _user_file_entry_for(generator, key)
    if not uf or uf.get("kind") != "single_file":
        return None
    return _user_dest_dir(generator, uf) / SINGLE_FILE_CONFIG_DIR


def single_file_config_present(generator, key: str) -> bool:
    """True when the model needs no base config or its config is on disk.

    The folder appears only once its fetch completed (see
    provision_single_file_config), so model_index.json in it means all of it.
    """
    config_dir = single_file_config_dir(generator, key)
    return config_dir is None or (config_dir / "model_index.json").is_file()


def single_file_config_missing(generator, key: str) -> bool:
    """The checkpoint is on disk but the base config it loads with is not."""
    uf = _user_file_entry_for(generator, key)
    if not uf or uf.get("kind") != "single_file":
        return False
    return _entry_files_present(generator, uf) and not single_file_config_present(generator, key)


class _SafetensorsCheckpoint(Mapping):
    """An open safetensors file as the mapping diffusers' model-type check reads:
    every key, and a tensor only when that key is looked up."""

    def __init__(self, handle):
        self._handle = handle
        self._keys = list(handle.keys())
        self._key_set = set(self._keys)

    def __getitem__(self, key):
        if key not in self._key_set:
            raise KeyError(key)
        return self._handle.get_tensor(key)

    def __contains__(self, key):
        return key in self._key_set

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)


def single_file_base_repo(checkpoint: Path) -> str:
    """The base pipeline repo diffusers pairs with this checkpoint.

    The choice from_single_file would make (fetch_diffusers_config), read from a
    safetensors file's keys and the few small tensors the check looks at rather
    than the whole file. Other formats load the way from_single_file loads them.
    """
    from diffusers.loaders.single_file_utils import fetch_diffusers_config

    if checkpoint.suffix.lower() == ".safetensors":
        from safetensors import safe_open
        with safe_open(str(checkpoint), framework="pt") as handle:
            found = fetch_diffusers_config(_SafetensorsCheckpoint(handle))
    else:
        from diffusers.models.modeling_utils import load_state_dict
        state = load_state_dict(str(checkpoint))
        while "state_dict" in state:
            state = state["state_dict"]
        found = fetch_diffusers_config(state)
    return found["pretrained_model_name_or_path"]


def provision_single_file_config(generator, sentinel: str) -> None:
    """Fetch the base config a single-file checkpoint loads with into its folder.

    Runs from Install, after the checkpoint is on disk. The files land in a
    ``.partial`` folder that is renamed into place only when the fetch
    completed, so an interrupted one never reads as installed; the next Install
    resumes it. Raises with the reason when the config cannot be fetched.
    """
    from huggingface_hub import snapshot_download

    target = single_file_config_dir(generator, sentinel)
    if target is None or single_file_config_present(generator, sentinel):
        return
    uf = _user_file_entry_for(generator, sentinel)
    checkpoint = _user_dest_dir(generator, uf) / uf["files"][0]["dst"]
    repo = single_file_base_repo(checkpoint)
    partial = target.with_name(target.name + ".partial")
    logger.info("Fetching the %s base config for %s into %s", repo, checkpoint.name, target)
    try:
        snapshot_download(
            repo_id=repo,
            allow_patterns=_SINGLE_FILE_CONFIG_PATTERNS,
            local_dir=str(partial),
        )
    except Exception as e:  # noqa: BLE001 — the message names what was being fetched
        raise RuntimeError(f"Fetching the {repo} base config for {checkpoint.name} failed: {e}") from e
    if not (partial / "model_index.json").is_file():
        raise RuntimeError(
            f"{repo} has no model_index.json, so {checkpoint.name} has no base config to load with."
        )
    partial.replace(target)


def download_companions(generator, sentinel: str, stop=None) -> tuple[bool, str | None]:
    """Fetch the companions a user ComfyUI model still lacks, through the registry downloader."""
    for eid in missing_companions(generator, sentinel):
        ok, err = _download_registry_entry(eid, stop)
        if not ok:
            return False, err
    return True, None


def _download_registry_entry(eid: str, stop=None) -> tuple[bool, str | None]:
    """Fetch one shipped registry entry's files where that entry declares them."""
    from backend.services.video_model_registry import (
        VIDEO_MODEL_REGISTRY, download_entry_files, is_model_installed, resolve_entry_dir,
    )

    def _check():
        if stop is not None and stop.is_set():
            raise RuntimeError("Download stalled")

    entry = VIDEO_MODEL_REGISTRY.get(eid)
    if not entry:
        return False, f"{eid} is not in the model registry"
    if is_model_installed(eid):
        return True, None
    local_dir = resolve_entry_dir(entry)
    local_dir.mkdir(parents=True, exist_ok=True)
    download_entry_files(entry, local_dir, check=_check)
    if not is_model_installed(eid):
        return False, f"Download of {eid} finished but its files are missing under {local_dir}"
    return True, None


def download_user_files(generator, sentinel: str, stop=None) -> tuple[bool, str | None]:
    """hf_hub_download each declared file into the dest dir, resuming partials,
    then the base config a single-file checkpoint loads with, then the
    companions a ComfyUI generation model renders with."""
    from huggingface_hub import hf_hub_download

    uf = (getattr(generator, "user_files", {}) or {}).get(sentinel)
    if not uf:
        return False, f"{sentinel} is not a user file entry"
    base = _user_dest_dir(generator, uf)
    base.mkdir(parents=True, exist_ok=True)
    try:
        for spec in uf.get("files") or []:
            if stop is not None and stop.is_set():
                return False, "Download stalled"
            want = base / spec["dst"]
            # A file already in place is kept. Install re-runs to fetch what is
            # still missing (a base config, companions), and a file moved off its
            # repo path would otherwise be fetched whole again.
            if want.is_file() and want.stat().st_size > 0:
                continue
            if uf.get("kind") == "encoder" and uf.get("shared_with"):
                ok, err = _download_registry_entry(uf["shared_with"], stop)
                if not ok:
                    return False, err
                continue
            # An encoder is fetched into its own folder: fetched straight into
            # clip/, a file named like a shipped one (clip_l.safetensors) would
            # replace the shipped file before being moved away.
            fetch_dir = want.parent / ".download" if uf.get("kind") == "encoder" else base
            got = Path(hf_hub_download(
                repo_id=uf["hf_repo"], filename=spec["src"], revision=uf.get("revision") or "main",
                local_dir=str(fetch_dir),
            ))
            if got.resolve() != want.resolve():
                want.parent.mkdir(parents=True, exist_ok=True)
                got.replace(want)
            if fetch_dir != base:
                import shutil
                shutil.rmtree(fetch_dir, ignore_errors=True)
        if uf.get("kind") == "lora":
            _write_lora_sidecar(base, uf)
        if stop is not None and stop.is_set():
            return False, "Download stalled"
        if uf.get("kind") == "single_file":
            provision_single_file_config(generator, sentinel)
            if stop is not None and stop.is_set():
                return False, "Download stalled"
        return download_companions(generator, sentinel, stop=stop)
    except Exception as e:  # noqa: BLE001 — the message is the diagnosis
        logger.error("user image file download failed for %s: %s", sentinel, e)
        return False, str(e)


def comfy_t2i_target(generator, model_id: str) -> dict | None:
    """How a model id renders on a ComfyUI text-to-image graph, or None.

    ``{"family", "tag", "unet"}``: ``tag`` is the model string _build_workflow
    dispatches on and ``unet`` the loader filename. A shipped id passes through
    (``unet`` None, the graph loads its registry file). A user entry supplies
    its own UNET and renders on the shipped entry comfy_graph_model names for
    it, the same entry its companions come from.
    """
    family = comfy_t2i_family(model_id)
    if not family:
        return None
    mid = (model_id or "").strip()
    if not is_user_model_id(mid):
        return {"family": family, "tag": mid, "unet": None}
    entry = (getattr(generator, "user_entries", None) or {}).get(mid) or {}
    files = entry.get("files") or []
    unet = (files[0].get("dst") if files else None) or None
    return {"family": family, "tag": comfy_graph_model(family, unet) or family, "unet": unet}


def _write_lora_sidecar(base: Path, uf: dict) -> None:
    """Sidecar the character pipeline reads for family routing, minus the subject."""
    from backend.services.media_model_registry import get_profile

    spec = GENERATION_FAMILIES.get(uf.get("family") or "", {})
    profile = get_profile(spec.get("like")) or {}
    for f in uf.get("files") or []:
        side = (base / f["dst"]).with_suffix(".json")
        if side.exists():
            continue
        side.write_text(json.dumps({
            "base_model_id": profile.get("id") or spec.get("like"),
            "lora_format": profile.get("lora_format"),
            "family": uf.get("family"),
            "user_lora": True,
            "schema_version": 2,
        }, indent=2), encoding="utf-8")


def resolve_user_loras(generator, model: str, adapters: list) -> tuple[list, float | None, str | None]:
    """User LoRA ids → (file paths, strength, None) or ([], None, message)."""
    if not adapters:
        return [], None, None
    entries = getattr(generator, "user_entries", {}) or {}
    try:
        from backend.services.stills_defaults import model_family
        fam = model_family(model)
        fam = {"krea2-turbo": "krea2", "krea2-raw": "krea2"}.get(fam, fam)
    except Exception:  # noqa: BLE001
        fam = None
    paths = []
    strength = None
    for raw in adapters:
        aid = raw.get("id") if isinstance(raw, dict) else raw
        aid = (aid or "").strip()
        if not aid:
            continue
        entry = entries.get(aid) or {}
        label = entry.get("name") or aid
        if entry.get("role") != "lora":
            return [], None, f"'{aid}' is not a LoRA you added."
        applies = entry.get("applies_to") or []
        if fam and applies and fam not in applies:
            return [], None, f"{label} is a {GENERATION_FAMILIES.get(applies[0], {}).get('label', applies[0])} LoRA and does not apply to this model."
        sentinel = user_sentinel(aid)
        if not user_files_present(generator, sentinel):
            return [], None, f"{label} is not installed. Open Manage Image Models to download it."
        uf = generator.user_files[sentinel]
        path = _user_dest_dir(generator, uf) / uf["files"][0]["dst"]
        if entry.get("family") == "zimage":
            from backend.services.zimage_lora_check import lora_file_problem
            problem = lora_file_problem(path)
            if problem:
                return [], None, (
                    f"{label} can't be used on Z-Image. {problem} "
                    "Remove it in Settings > Image (Manage Image Generation Models)."
                )
        paths.append(str(path))
        s = raw.get("strength") if isinstance(raw, dict) else None
        s = float(s if s is not None else entry.get("strength") or DEFAULT_LORA_STRENGTH)
        strength = s if strength is None else max(strength, s)
    return paths, strength, None


def catalog_rows(generator) -> list:
    """Rows for the LoRA side of the models list (the picker never shows these)."""
    rows = []
    for mid, entry in (getattr(generator, "user_entries", {}) or {}).items():
        if entry.get("role") != "lora":
            continue
        rows.append({
            "id": mid,
            "name": entry.get("name") or mid,
            "description": entry.get("description") or "",
            "type": "lora",
            "family": entry.get("family"),
            "applies_to": list(entry.get("applies_to") or []),
            "hf_repo": entry.get("hf_repo"),
            "size_gb": entry.get("size_gb") or 0.0,
            "strength": entry.get("strength") or DEFAULT_LORA_STRENGTH,
            "engine": entry.get("engine") or "offline",
            "is_downloaded": user_files_present(generator, user_sentinel(mid)),
            "user": True,
        })
    return rows


def encoder_rows(generator) -> list:
    """Rows for the text-encoder side of the models list and the page's picker."""
    rows = []
    for mid, entry in (getattr(generator, "user_entries", {}) or {}).items():
        if entry.get("role") != "encoder":
            continue
        slot_spec = encoder_slots(entry.get("family") or "").get(entry.get("slot") or "") or {}
        rows.append({
            "id": mid,
            "name": entry.get("name") or mid,
            "description": entry.get("description") or "",
            "type": "encoder",
            "family": entry.get("family"),
            "slot": entry.get("slot"),
            "slot_label": slot_spec.get("label") or entry.get("slot"),
            "applies_to": list(entry.get("applies_to") or []),
            "hf_repo": entry.get("hf_repo"),
            "size_gb": entry.get("size_gb") or 0.0,
            "shared_with": entry.get("shared_with"),
            "engine": "comfy",
            "is_downloaded": user_files_present(generator, user_sentinel(mid)),
            "user": True,
        })
    return rows


def resolve_user_encoders(generator, model: str, encoder_ids: list) -> tuple[dict, str | None]:
    """User text-encoder ids → ``({slot: loader filename}, None)`` for a render
    on ``model``, or ``({}, message)``.

    Each one must be a text encoder the person added for this model's family,
    one per slot, installed, and (read from the file's header) the architecture
    its slot reads.
    """
    ids = [
        str((raw.get("id") if isinstance(raw, dict) else raw) or "").strip()
        for raw in (encoder_ids or [])
    ]
    ids = [i for i in ids if i]
    if not ids:
        return {}, None
    family = comfy_t2i_family(model)
    slots = encoder_slots(family or "")
    if not slots:
        return {}, (
            "This model does not take a replacement text encoder. "
            "Pick a FLUX model, or leave Text encoder on the shipped one."
        )
    entries = getattr(generator, "user_entries", {}) or {}
    chosen: dict = {}
    for eid in ids:
        entry = entries.get(eid) or {}
        label = entry.get("name") or eid
        if entry.get("role") != "encoder":
            return {}, f"'{eid}' is not a text encoder you added."
        if entry.get("family") != family:
            fam_label = GENERATION_FAMILIES.get(entry.get("family") or "", {}).get("label", entry.get("family"))
            return {}, f"{label} is a {fam_label} text encoder and does not apply to this model."
        slot = entry.get("slot")
        slot_spec = slots.get(slot or "")
        if not slot_spec:
            return {}, f"{label} names a text-encoder slot this model does not have."
        if slot in chosen:
            return {}, f"Two text encoders were picked for the {slot_spec['label']} slot. Pick one."
        sentinel = user_sentinel(eid)
        if not user_files_present(generator, sentinel):
            return {}, f"{label} is not installed. Open Manage Image Models to download it."
        uf = generator.user_files[sentinel]
        dst = uf["files"][0]["dst"]
        problem = te_check.local_mismatch(_user_dest_dir(generator, uf) / dst, slot_spec["arch"], slot_spec["label"])
        if problem:
            return {}, f"{label} can't be used. {problem}"
        chosen[slot] = dst
    return chosen, None
