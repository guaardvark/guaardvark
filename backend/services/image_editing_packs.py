"""The image-editing packs a person installs from Manage Image Models.

One table declares which registry entries make a chat photo tool work, and
every refusal message, the /imagemodel listing and the modal's "Image editing"
section read it. A tool that is missing its pack names this modal, not the
Video one, and never downloads on its own.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

INSTALL_HINT = "Open Manage Image Models, Image editing, and install it."

# id doubles as the registry entry the Install button starts; `requires` on that
# entry pulls the companions (Qwen's encoder and VAE, PuLID's face files).
PACKS = (
    {
        "id": "qwen-image-edit",
        "name": "Qwen-Image-Edit 2509 (FP8)",
        "short": "Qwen-Image-Edit",
        "description": "Instruction edits, inpaint and outpaint in chat, with up to three "
                       "reference images. Chat prefers this pack when it is installed. "
                       "Apache-2.0.",
        "tools": ("edit_image", "inpaint_image", "outpaint_image"),
    },
    {
        "id": "flux-kontext-dev",
        "name": "FLUX.1 Kontext [dev]",
        "short": "FLUX.1 Kontext",
        "description": "Instruction edits; the fallback when Qwen-Image-Edit is not "
                       "installed. FLUX.1 non-commercial licence.",
        "tools": ("edit_image", "inpaint_image", "outpaint_image"),
    },
    {
        "id": "moss-image-edit",
        "name": "MOSS-Image Edit",
        "short": "MOSS-Image Edit",
        "description": "Instruction edits of one photo with MOSS-Image's edit LoRA: clothing, "
                       "objects and colours, with the rest of the photo kept. Installs MOSS-Image "
                       "when it is not there yet. Apache-2.0.",
        "tools": ("edit_image",),
    },
    {
        "id": "pulid-flux",
        "name": "PuLID identity on FLUX.1-dev",
        "short": "the PuLID identity pack",
        "description": "A new scene that keeps the face from one photo. Installs PuLID, "
                       "its face analysis files and EVA02-CLIP, and FLUX.1-dev when it "
                       "is not there yet.",
        "tools": ("generate_identity",),
    },
    {
        "id": "bgremove-birefnet",
        "name": "Background removal, BiRefNet general",
        "short": "BiRefNet general",
        "description": "Cuts the subject out with a clean alpha edge. 1024 px matting, a "
                       "few seconds on CPU. MIT.",
        "tools": ("remove_background",),
    },
    {
        "id": "bgremove-u2net",
        "name": "Background removal, u2net",
        "short": "u2net",
        "description": "The small cut-out model: under a second on CPU, softer edges. "
                       "Apache-2.0.",
        "tools": ("remove_background",),
    },
)

TOOL_LABELS = {
    "edit_image": "Image editing",
    "inpaint_image": "Inpainting",
    "outpaint_image": "Outpainting",
    "generate_identity": "Identity generation",
    "remove_background": "Background removal",
}


def _enabled(pack: dict) -> bool:
    flag = pack.get("flag")
    return not flag or os.environ.get(flag) == "1"


def enabled_packs() -> List[dict]:
    return [p for p in PACKS if _enabled(p)]


def pack_by_id(pack_id: str) -> Optional[dict]:
    for pack in enabled_packs():
        if pack["id"] == pack_id:
            return pack
    return None


def packs_for_tool(tool: str) -> List[dict]:
    return [p for p in PACKS if tool in p["tools"]]


def _video_api():
    from backend.api import batch_video_generation_api as video_api
    return video_api


def pack_plan(pack: dict) -> List[str]:
    return _video_api()._resolve_download_plan(pack["id"])


def pack_installed(pack: dict) -> bool:
    api = _video_api()
    return all(api._check_model_downloaded(eid) for eid in pack_plan(pack))


def pack_rows() -> List[Dict]:
    """Rows for the modal's Image editing section, in table order."""
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY

    api = _video_api()
    rows = []
    for pack in enabled_packs():
        plan = pack_plan(pack)
        present = {eid: api._check_model_downloaded(eid) for eid in plan}
        installed = all(present.values())
        size = round(sum(float(VIDEO_MODEL_REGISTRY[eid].get("size_gb") or 0) for eid in plan), 2)
        # What Install would fetch now: companions already on disk (flux-dev
        # under PuLID, say) are not counted twice.
        download = round(sum(
            float(VIDEO_MODEL_REGISTRY[eid].get("size_gb") or 0)
            for eid in plan if not present[eid]
        ), 2)
        rows.append({
            "id": pack["id"],
            "path": f"comfy:{pack['id']}",
            "name": pack["name"],
            "description": pack["description"],
            "size_gb": size,
            "download_gb": download,
            "installed": installed,
            "is_downloaded": installed,
            "missing_files": [] if installed else api._missing_check_files(pack["id"]),
            "tools": list(pack["tools"]),
            "entries": plan,
            "group": "editing",
        })
    return rows


def tool_ready(tool: str) -> bool:
    return any(pack_installed(p) for p in packs_for_tool(tool))


def missing_message(tool: str) -> str:
    """One sentence shape for every photo tool that lacks its pack."""
    label = TOOL_LABELS.get(tool, tool)
    packs = packs_for_tool(tool)
    if not packs:
        return f"{label} is not available on this install."
    names = " or ".join(p["short"] for p in packs)
    return f"{label} needs {names}, which is not installed. {INSTALL_HINT}"
