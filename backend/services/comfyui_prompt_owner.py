"""Stop the ComfyUI prompts one Guaardvark job queued, and nothing else.

ComfyUI is shared: the Studio, batch runs, the night shift, the chat and
anyone using ComfyUI directly queue on the same server. A bare ``/interrupt``
stops whatever is sampling and ``/queue {"clear": true}`` drops everyone's
pending work, so neither is used. A job tags each prompt it queues with an
owner string in ``extra_data``; a cancel, from whichever process, reads
``/queue`` for that owner's prompts and stops only those.
"""
from __future__ import annotations

import logging
from typing import Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

OWNER_KEY = "guaardvark_owner"


def tag_payload(payload: dict, owner: Optional[str]) -> dict:
    """Mark a ``/prompt`` payload as queued by ``owner`` (no-op without one)."""
    if owner:
        extra = payload.get("extra_data")
        if not isinstance(extra, dict):
            extra = {}
            payload["extra_data"] = extra
        extra[OWNER_KEY] = str(owner)
    return payload


def owned_prompt_ids(comfy_url: str, owner: str) -> List[str]:
    """Prompt ids, running first, that ComfyUI holds for ``owner``.

    Queue items are ``[number, prompt_id, prompt, extra_data, outputs]``.
    An unreachable ComfyUI holds nothing.
    """
    if not owner:
        return []
    try:
        resp = requests.get(f"{comfy_url.rstrip('/')}/queue", timeout=5)
        resp.raise_for_status()
        queue = resp.json() or {}
    except Exception as e:  # noqa: BLE001 - a cancel never fails over a lookup
        logger.warning("ComfyUI queue lookup for %s failed: %s", owner, e)
        return []
    ids: List[str] = []
    for section in ("queue_running", "queue_pending"):
        for item in queue.get(section) or []:
            try:
                extra = item[3] if isinstance(item[3], dict) else {}
                if extra.get(OWNER_KEY) == owner and item[1] not in ids:
                    ids.append(item[1])
            except (IndexError, TypeError, AttributeError):
                continue
    return ids


def stop_prompts(comfy_url: str, prompt_ids: Iterable[str], *, reason: str = "cancel") -> List[str]:
    """Delete each prompt from the pending queue, then interrupt it if running.

    The delete goes first: ``/interrupt`` with a ``prompt_id`` acts only on a
    running prompt, so in the other order a prompt that started between the two
    calls would render to the end. ``reason`` is logged with each stop.
    Returns the ids ComfyUI acknowledged.
    """
    base = comfy_url.rstrip("/")
    acked: List[str] = []
    for pid in prompt_ids:
        if not pid:
            continue
        try:
            requests.post(f"{base}/queue", json={"delete": [pid]}, timeout=5)
        except Exception as e:  # noqa: BLE001
            logger.debug("ComfyUI queue delete for %s failed (non-fatal): %s", pid, e)
        try:
            requests.post(f"{base}/interrupt", json={"prompt_id": pid}, timeout=5)
            acked.append(pid)
            logger.info("Stopped ComfyUI prompt %s (%s)", pid, reason)
        except Exception as e:  # noqa: BLE001
            logger.warning("ComfyUI interrupt for prompt %s failed: %s", pid, e)
    return acked


def job_owner(kind: str, native_id) -> str:
    """The owner tag for one job, e.g. ``music_video:12``."""
    return f"{kind}:{native_id}"
