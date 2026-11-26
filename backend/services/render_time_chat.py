"""The chat model while rendering.

A render or training job sizes itself for the GPU it finds. A chat model that
loads beside it takes memory the job counted on, and the job runs out. So
while such a job holds the card, a chat or RAG turn is answered by a small
model on the CPU (``num_gpu: 0``) with thinking off, and the chat model itself
is left alone. With no job on the card nothing here applies.

The person's choice is the ``chat_model_while_rendering`` setting: ``auto``
(the default), ``same`` (keep answering with the chat model, for a machine
with VRAM to spare or a second GPU) or a model name. Which models qualify, the
preferred ones and the limits are declared in ``model_capability_data``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SETTING_KEY = "chat_model_while_rendering"
AUTO = "auto"
SAME = "same"


@dataclass(frozen=True)
class RenderTimeTurn:
    """How one chat turn is answered while a job holds the GPU."""
    model: str
    on_cpu: bool
    reason: str
    status: str
    num_ctx: Optional[int]
    keep_alive: Any

    @property
    def options(self) -> Dict[str, int]:
        """Ollama ``options`` every request of the turn carries."""
        return {"num_gpu": 0} if self.on_cpu else {}


def honours_think_off(model_name: str) -> Optional[bool]:
    """Whether ``model_name`` answers without reasoning when sent think:false.

    The declared row for the tag, else for the name without its tag
    (THINK_FALSE_HONOURED). None when nothing is declared.
    """
    from backend.services.model_capability_data import THINK_FALSE_HONOURED
    name = (model_name or "").strip().lower()
    if not name:
        return None
    if ":" not in name:
        name += ":latest"
    if name in THINK_FALSE_HONOURED:
        return THINK_FALSE_HONOURED[name]
    return THINK_FALSE_HONOURED.get(name.rsplit(":", 1)[0])


def qualifies(model_name: str) -> bool:
    """A model chat can answer with on the CPU: it chats, calls tools, is not
    declared unusable (RENDER_TIME_NOT_OFFERED), and either does not reason or
    is declared to stop when told to."""
    from backend.services.model_capabilities import capabilities_for
    from backend.services.model_capability_data import RENDER_TIME_NOT_OFFERED
    base = (model_name or "").strip().lower().rsplit(":", 1)[0]
    if base in RENDER_TIME_NOT_OFFERED:
        return False
    rec = capabilities_for(model_name, with_vision=False)
    if not rec.exists or not rec.completion or rec.embedding or rec.decision or not rec.tools:
        return False
    declared = honours_think_off(model_name)
    if declared is not None:
        return declared
    return not (rec.thinking or rec.thinks_by_name)


def offered(tags: Optional[list] = None) -> List[Dict[str, Any]]:
    """Installed models that qualify, smallest first: ``name``, ``size``
    (bytes on disk), ``parameter_count`` and ``parameter_size``. Two names for
    one build are listed once, under the name that is not ``:latest``.
    """
    from backend.services.model_capabilities import capabilities_for
    from backend.utils.ollama_resource_manager import _ollama_tags
    rows, seen = [], set()
    entries = tags if tags is not None else _ollama_tags()
    for entry in sorted(entries, key=lambda e: str(e.get("name") or "").endswith(":latest")):
        name = str(entry.get("name") or entry.get("model") or "")
        digest = str(entry.get("digest") or name)
        if not name or digest in seen:
            continue
        try:
            if not qualifies(name):
                continue
            params = capabilities_for(name, with_vision=False).parameter_count
        except Exception as e:  # noqa: BLE001 - one unreadable model must not empty the list
            logger.debug("Could not read %s for the render-time list: %s", name, e)
            continue
        seen.add(digest)
        rows.append({
            "name": name,
            "size": int(entry.get("size") or 0),
            "parameter_count": int(params or 0),
            "parameter_size": str((entry.get("details") or {}).get("parameter_size") or ""),
        })
    rows.sort(key=lambda r: (r["size"], r["name"]))
    return rows


def automatic_model(rows: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """What ``auto`` answers with: the first preferred model that is offered,
    else the smallest one offered within RENDER_TIME_AUTO_MAX_PARAMS, else None."""
    from backend.services.model_capability_data import (
        RENDER_TIME_AUTO_MAX_PARAMS,
        RENDER_TIME_PREFERRED_MODELS,
    )
    rows = offered() if rows is None else rows
    names = {r["name"] for r in rows}
    for preferred in RENDER_TIME_PREFERRED_MODELS:
        if preferred in names:
            return preferred
    for row in rows:
        if 0 < row["parameter_count"] <= RENDER_TIME_AUTO_MAX_PARAMS:
            return row["name"]
    return None


def saved_choice() -> str:
    """``auto``, ``same`` or a model name."""
    try:
        from backend.utils.settings_utils import get_setting
        value = (get_setting(SETTING_KEY, default=AUTO) or "").strip()
    except Exception as e:  # noqa: BLE001 - an unreadable setting is the default
        logger.debug("Could not read %s: %s", SETTING_KEY, e)
        value = ""
    return value or AUTO


def chosen_model(choice: Optional[str] = None) -> Optional[str]:
    """The model the choice names, None for ``same`` or when nothing qualifies.

    A named model is used as the person chose it, whatever its size. One that
    is no longer installed, or no longer qualifies, falls back to ``auto``.
    """
    choice = saved_choice() if choice is None else choice
    if choice == SAME:
        return None
    if choice != AUTO:
        from backend.utils.ollama_resource_manager import model_installed
        if model_installed(choice) and qualifies(choice):
            return choice
        logger.info("%s names %s, which is not installed or not offered; using the automatic choice",
                    SETTING_KEY, choice)
    return automatic_model()


def _job_words(reason: str) -> str:
    """What holds the GPU, for the status line. The lease is shared by renders
    and training, so a held lease is called a render only when ComfyUI is
    working on one."""
    if "ComfyUI" in (reason or ""):
        return "a render"
    try:
        from backend.utils.gpu_check import comfyui_generating
        if comfyui_generating():
            return "a render"
    except Exception as e:  # noqa: BLE001 - the wider wording is still true
        logger.debug("ComfyUI probe failed: %s", e)
    return "a render or training job"


def for_turn(chat_model: str) -> Optional[RenderTimeTurn]:
    """How to answer a turn that starts now, or None to answer as usual.

    None when no job holds the GPU, when the choice is ``same``, when the chat
    model is already on the card (it answers from there and loads nothing), or
    when no model qualifies. A render-time model that is itself on the card
    answers from there; otherwise it is asked for on the CPU.
    """
    try:
        from backend.utils.gpu_check import gpu_reserved_reason
        reason = gpu_reserved_reason()
    except Exception as e:  # noqa: BLE001 - without the probe no job is known to hold the card
        logger.debug("GPU hold probe failed: %s", e)
        return None
    if reason is None:
        return None
    try:
        from backend.services.model_capability_data import RENDER_TIME_KEEP_ALIVE, RENDER_TIME_NUM_CTX
        from backend.utils.ollama_resource_manager import resident_entry
        if saved_choice() == SAME:
            return None
        if chat_model and resident_entry(chat_model) is not None:
            return None
        model = chosen_model()
        if not model:
            logger.info("No installed model qualifies as the chat model while rendering; "
                        "%s answers as usual (%s)", chat_model, reason)
            return None
        on_cpu = resident_entry(model) is None
    except Exception as e:  # noqa: BLE001 - a failed lookup answers as usual
        logger.warning("Could not pick the chat model while rendering: %s", e)
        return None
    where = " on the CPU" if on_cpu else ""
    status = f"Answering with {model}{where} while {_job_words(reason)} uses the GPU"
    logger.info("%s (chat model %s; %s)", status, chat_model, reason)
    if on_cpu:
        return RenderTimeTurn(model, True, reason, status, RENDER_TIME_NUM_CTX, RENDER_TIME_KEEP_ALIVE)
    from backend.config import get_chat_keep_alive
    return RenderTimeTurn(model, False, reason, status, None, get_chat_keep_alive())


def settings_state() -> Dict[str, Any]:
    """What Settings shows: the saved choice, the models offered and what
    ``auto`` resolves to on this machine now."""
    rows = offered()
    return {"choice": saved_choice(), "models": rows, "automatic": automatic_model(rows)}
