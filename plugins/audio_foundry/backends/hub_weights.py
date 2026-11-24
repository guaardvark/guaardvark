"""Refuse a generation load when the Hugging Face snapshot is not local.

The Audio Studio Manage-models modal is the only path that fetches these
repos. `from_pretrained` / `hf_hub_download` would otherwise pull gigabytes
on the first Generate. The sidecar also runs with HF_HUB_OFFLINE=1
(scripts/start.sh), so a lookup that slips past these checks fails instead of
downloading.
"""

from __future__ import annotations

from typing import Optional

INSTALL_HINT = (
    "Open Audio Studio → Manage models and Install this model. "
    "Generation never downloads on its own."
)


class WeightsNotInstalled(RuntimeError):
    """The weights are not on this machine and this code path will not fetch them."""


def cached_hub_file(repo_id: str, filename: str) -> Optional[str]:
    """Local path of ``filename`` from ``repo_id`` in the HF cache, or None.

    Reads the cache only; never touches the network.
    """
    try:
        from huggingface_hub import try_to_load_from_cache
        from huggingface_hub.file_download import _CACHED_NO_EXIST
    except Exception:  # pragma: no cover
        return None
    try:
        hit = try_to_load_from_cache(repo_id, filename)
    except Exception:
        return None
    if not isinstance(hit, str) or hit is _CACHED_NO_EXIST:
        return None
    return hit


def require_hub_files(repo_id: str, files: list[str], purpose: str) -> dict[str, str]:
    """Local paths of ``files``, keyed by name. Raise WeightsNotInstalled if any is
    missing or cut short."""
    try:
        import huggingface_hub  # noqa: F401
    except Exception as e:  # pragma: no cover
        raise WeightsNotInstalled(
            f"{purpose}: cannot probe the Hugging Face cache ({e}). {INSTALL_HINT}"
        ) from e

    from backends.model_file_integrity import file_incomplete

    found: dict[str, str] = {}
    missing: list[str] = []
    for name in files:
        hit = cached_hub_file(repo_id, name)
        if hit is None:
            missing.append(name)
            continue
        reason = file_incomplete(hit)
        if reason:
            raise WeightsNotInstalled(
                f"{purpose}: {name} of '{repo_id}' is incomplete on this machine "
                f"({reason}). {INSTALL_HINT}"
            )
        found[name] = hit
    if missing:
        raise WeightsNotInstalled(
            f"{purpose}: weights for '{repo_id}' are not on this machine "
            f"(missing {missing[0]}). {INSTALL_HINT}"
        )
    return found
