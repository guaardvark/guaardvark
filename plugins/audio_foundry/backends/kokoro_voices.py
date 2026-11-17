"""The Kokoro voices Audio Foundry offers, and the files each one needs.

The list lives in ``kokoro_voices.json`` beside this module. The sidecar reads
it for GET /voices and to refuse an unknown voice id; the main backend reads
the same file (backend/services/audio_foundry_models.py) to decide whether
Kokoro counts as installed and to check voice ids from MCP clients. Adding a
voice here makes Manage models report Kokoro as not installed until its voice
pack is on disk, which is the point: Install fetches it, generation never does.

``english_g2p`` is spaCy's English pipeline. Kokoro's English phonemizer
(misaki ``en.G2P``) loads it for the American and British pipelines and runs
``spacy.cli.download`` when it is absent, which pip-installs from GitHub in the
middle of a generation request. The Manage-models Install puts the pinned
wheel into this plugin's venv instead.

Each group names its language (``lang``, Kokoro's lang_code). ``languages``
gives the label, and for a language whose phonemizer is not part of the base
install (Japanese, Mandarin Chinese: misaki's ``ja`` and ``zh`` extras) the
``language_packs`` entry Install adds: a requirements file with pinned hashes
and the distributions it provides. The other languages speak through the
espeak-ng library that comes with kokoro. A voice whose pack is missing stays
listed and is refused with the Install hint.

``max_phonemes`` is the longest phoneme string the model reads in one pass
(kokoro's KPipeline truncates at 510). ``pack_phonemes`` is how much text the
non-English languages are given per pass: sentences are joined up to it, and
the entry beside it records what longer passes measured. ``withheld`` lists
upstream voices left out, each with the reason and the measurement. ``sha256``
values are the Hugging Face LFS digests of the files at ``hf_revision``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("kokoro_voices.json")

# Every catalog id has this shape. Checking it first keeps paths ('x.pt',
# which KPipeline would torch.load) and blends ('a,b') out before any lookup.
VOICE_ID_PATTERN = re.compile(r"^[a-z]{2}_[a-z]+$")


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def hf_repo() -> str:
    return load_catalog()["hf_repo"]


def config_file() -> str:
    return load_catalog()["config_file"]


def weights_file() -> str:
    return load_catalog()["weights_file"]


def model_files() -> list[str]:
    return [config_file(), weights_file()]


def voice_ids() -> list[str]:
    return [v["id"] for g in load_catalog()["groups"] for v in g["voices"]]


def default_voice() -> str:
    return load_catalog()["default"]


def voice_file(voice_id: str) -> str:
    """Repo-relative path of a voice pack, e.g. ``voices/af_heart.pt``."""
    return load_catalog()["voice_file"].format(voice=voice_id)


def english_g2p() -> dict[str, Any]:
    return dict(load_catalog()["english_g2p"])


def max_phonemes() -> int:
    return int(load_catalog()["max_phonemes"])


def pack_phonemes() -> int:
    return int(load_catalog()["pack_phonemes"])


def voice_language(voice_id: str) -> str:
    """Kokoro lang_code of a catalog voice (its group's ``lang``)."""
    for group in load_catalog()["groups"]:
        if any(v["id"] == voice_id for v in group["voices"]):
            return group["lang"]
    raise UnknownVoice(f"unknown Kokoro voice {voice_id!r}")


def language_label(lang_code: str) -> str:
    return load_catalog()["languages"][lang_code]["label"]


def language_pack(lang_code: str) -> dict[str, Any] | None:
    """The pack a language needs beyond the base install, with its ``id``; None
    when the base install speaks it."""
    cat = load_catalog()
    pack_id = cat["languages"][lang_code].get("pack")
    if not pack_id:
        return None
    return {"id": pack_id, **cat["language_packs"][pack_id]}


def language_packages(lang_code: str) -> list[str]:
    """Distributions the language's phonemizer imports that Install provides."""
    g2p = english_g2p()
    if lang_code in g2p["lang_codes"]:
        return [g2p["package"]]
    pack = language_pack(lang_code)
    return list(pack["packages"]) if pack else []


def missing_language_packages(lang_code: str) -> list[str]:
    """Those of ``language_packages`` this interpreter cannot find.

    importlib.metadata is the lookup spaCy's is_package uses before misaki
    falls back to a pip download.
    """
    from importlib import metadata

    missing = []
    for package in language_packages(lang_code):
        try:
            metadata.distribution(package)
        except metadata.PackageNotFoundError:
            missing.append(package)
    return missing


def voice_groups(pack_installed, missing_packages=missing_language_packages) -> list[dict[str, Any]]:
    """The catalog's groups as GET /voices lists them.

    ``pack_installed(voice_id)`` says whether a voice pack is on this machine.
    Each voice carries ``installed`` and ``language_installed``; each group the
    language's name and, when its phonemizer is missing, the Manage-models row
    that installs it (``install``: ``{id, name}``). The main backend answers
    for a stopped plugin and passes its own ``missing_packages``, which looks
    in the plugin venv from outside.
    """
    groups = []
    for group in load_catalog()["groups"]:
        lang = group["lang"]
        ready = not missing_packages(lang)
        pack = language_pack(lang)
        groups.append({
            "label": group["label"],
            "lang": lang,
            "language": language_label(lang),
            "language_installed": ready,
            "install": None if ready or not pack else {"id": pack["id"], "name": pack["name"]},
            "voices": [
                {"id": v["id"], "label": v["label"],
                 "installed": bool(pack_installed(v["id"])),
                 "language_installed": ready}
                for v in group["voices"]
            ],
        })
    return groups


class UnknownVoice(ValueError):
    """The voice id is not one of the Kokoro voices this plugin offers."""


def check_voice_id(voice_id: str) -> str:
    """Return ``voice_id`` when it is a catalog voice, else raise UnknownVoice."""
    voice_id = (voice_id or "").strip()
    if not VOICE_ID_PATTERN.match(voice_id) or voice_id not in voice_ids():
        raise UnknownVoice(
            f"unknown Kokoro voice {voice_id!r}; choose one of: {', '.join(voice_ids())}"
        )
    return voice_id
