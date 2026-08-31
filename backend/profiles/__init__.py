"""Profiles: one switch that sets the product shape.

A profile is a JSON document that sets, together, what today is scattered
across ``.env`` feature flags, ``plugin.json`` defaults, ``start.sh`` flags and
``brand.jsx``: which subsystems are on, which plugins ship enabled, which
startup work is skipped, which nav items are listed, where the app lands.

Three kinds:

- ``workstation`` — today's full product. Carries no overrides at all, so a
  machine with no ``GUAARDVARK_PROFILE`` behaves byte-for-byte as before.
- ``creator`` — the media workflow (image, video, audio, Film Crew, LoRA,
  upscaling) with agents, the knowledge index, outreach and automation left
  installed but unlisted and off by default.
- a *distribution* — the profile an extension ships as
  ``extensions/<id>/profile.json``, selected by the extension id.

Two rules every consumer honours:

- **An explicit value always wins.** Profile values are applied with
  ``setdefault``: a key already in ``.env`` or the environment, a CLI flag, a
  plugin toggle, or a DB setting overrides the profile. A profile is a starting
  point, never a ceiling.
- **Hidden means unlisted, never removed.** Routes, APIs and tests stay live;
  the profile only decides what the sidebar lists and where ``/`` lands.

This module is stdlib-only and imported from ``backend/config.py`` before any
feature flag is read (several services re-parse ``os.environ`` themselves), and
loaded by path from ``start.sh`` before the venv exists.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, MutableMapping, Optional

logger = logging.getLogger(__name__)

PROFILE_ENV = "GUAARDVARK_PROFILE"
PLUGIN_DEFAULTS_ENV = "GUAARDVARK_PROFILE_PLUGIN_DEFAULTS"
DEFAULT_PROFILE = "workstation"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Keys a profile may carry. Unknown keys are reported, not fatal, so an older
# build can read a newer profile.
KNOWN_KEYS = frozenset({
    "name", "label", "description", "env", "plugins", "startup", "nav",
    "landing_route", "chat_surfaces", "brand", "default_models",
})
# startup key -> the shell variable start.sh consults when no flag was passed.
STARTUP_KEYS = {
    "voice_check": "GUAARDVARK_PROFILE_VOICE_CHECK",
    "bootstrap_models": "GUAARDVARK_BOOTSTRAP_MODELS",
}
# default_models key -> the env var the existing model selection already honours.
DEFAULT_MODEL_ENV = {"chat": "GUAARDVARK_DEFAULT_LLM", "embed": "GUAARDVARK_EMBEDDING_MODEL"}


@dataclass
class Profile:
    name: str
    label: str = ""
    description: str = ""
    env: dict[str, str] = field(default_factory=dict)
    plugins: dict[str, bool] = field(default_factory=dict)
    startup: dict[str, bool] = field(default_factory=dict)
    hidden_routes: list[str] = field(default_factory=list)
    landing_route: Optional[str] = None
    chat_surfaces: Optional[list[str]] = None
    brand: dict[str, Optional[str]] = field(default_factory=dict)
    default_models: dict[str, Optional[str]] = field(default_factory=dict)
    source: str = "core"          # "core" | "extension"
    path: Optional[Path] = None
    fallback_reason: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_default(self) -> bool:
        return self.name == DEFAULT_PROFILE

    def public_dict(self) -> dict[str, Any]:
        """What the frontend needs — no env values, no file paths."""
        out: dict[str, Any] = {
            "name": self.name,
            "label": self.label or self.name,
            "description": self.description,
            "source": self.source,
            "hidden_routes": list(self.hidden_routes),
            "landing_route": self.landing_route,
            "chat_surfaces": list(self.chat_surfaces) if self.chat_surfaces is not None else None,
            "brand": {k: v for k, v in self.brand.items() if v},
        }
        if self.fallback_reason:
            out["fallback_reason"] = self.fallback_reason
        return out


# ─── locating profiles ────────────────────────────────────────────────────────

def core_profiles_dir() -> Path:
    return Path(__file__).resolve().parent


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def extensions_dir(root: Optional[Path] = None) -> Path:
    return (root or repo_root()) / "extensions"


def available_profiles(root: Optional[Path] = None) -> dict[str, tuple[str, Path]]:
    """name -> (source, path). Core profiles first; an extension's profile.json
    is registered under the extension's folder name. Underscore folders are
    templates and never load (same rule as plugins)."""
    found: dict[str, tuple[str, Path]] = {}
    for p in sorted(core_profiles_dir().glob("*.json")):
        found[p.stem] = ("core", p)
    ext_dir = extensions_dir(root)
    if ext_dir.is_dir():
        for d in sorted(ext_dir.iterdir()):
            if not d.is_dir() or d.name.startswith(("_", ".")):
                continue
            p = d / "profile.json"
            if p.is_file() and d.name not in found:
                found[d.name] = ("extension", p)
    return found


def requested_name(environ: Optional[MutableMapping[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    return (env.get(PROFILE_ENV) or "").strip() or DEFAULT_PROFILE


# ─── parsing ──────────────────────────────────────────────────────────────────

def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _env_str(value: Any) -> str:
    # Booleans become the lowercase strings every GUAARDVARK_* flag parses.
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def parse_profile(data: dict[str, Any], *, name: str, source: str, path: Optional[Path]) -> Profile:
    warnings: list[str] = []
    unknown = sorted(set(data) - KNOWN_KEYS)
    if unknown:
        warnings.append(f"unknown keys ignored: {', '.join(unknown)}")

    env: dict[str, str] = {}
    for key, value in (data.get("env") or {}).items():
        if not _ENV_KEY_RE.match(str(key)):
            warnings.append(f"env key {key!r} is not an environment variable name; ignored")
            continue
        env[str(key)] = _env_str(value)

    plugins = {str(k): _as_bool(v) for k, v in (data.get("plugins") or {}).items()}

    startup: dict[str, bool] = {}
    for key, value in (data.get("startup") or {}).items():
        if key not in STARTUP_KEYS:
            warnings.append(f"startup key {key!r} unknown; ignored")
            continue
        startup[key] = _as_bool(value)

    nav = data.get("nav") or {}
    hidden = [str(r) for r in (nav.get("hidden") or [])]
    for extra in sorted(set(nav) - {"hidden"}):
        warnings.append(f"nav key {extra!r} unknown; a profile can only hide routes")

    default_models: dict[str, Optional[str]] = {}
    for key, value in (data.get("default_models") or {}).items():
        if key not in DEFAULT_MODEL_ENV:
            warnings.append(f"default_models key {key!r} unknown; ignored")
            continue
        default_models[key] = str(value) if value else None

    chat_surfaces = data.get("chat_surfaces")
    return Profile(
        name=name,
        label=str(data.get("label") or name),
        description=str(data.get("description") or ""),
        env=env,
        plugins=plugins,
        startup=startup,
        hidden_routes=hidden,
        landing_route=(str(data["landing_route"]) if data.get("landing_route") else None),
        chat_surfaces=([str(s) for s in chat_surfaces] if isinstance(chat_surfaces, list) else None),
        brand={k: (str(v) if v else None) for k, v in (data.get("brand") or {}).items()},
        default_models=default_models,
        source=source,
        path=path,
        warnings=warnings,
    )


def load_profile(name: Optional[str] = None, *, root: Optional[Path] = None,
                 environ: Optional[MutableMapping[str, str]] = None) -> Profile:
    """Load ``name`` (default: the requested one). Never raises: an unknown
    name or unreadable file falls back to ``workstation`` and says why in
    ``fallback_reason`` — boot must not die on a typo, but it must not be
    silent either."""
    name = (name or requested_name(environ)).strip()
    if not _NAME_RE.match(name):
        return _fallback(f"profile name {name!r} is not valid", root)
    found = available_profiles(root)
    if name not in found:
        return _fallback(f"profile {name!r} not found (available: {', '.join(sorted(found)) or 'none'})", root)
    source, path = found[name]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("top level must be an object")
    except Exception as e:
        return _fallback(f"profile {name!r} unreadable ({e})", root)
    profile = parse_profile(data, name=name, source=source, path=path)
    for w in profile.warnings:
        logger.warning("profile %s: %s", name, w)
    return profile


def _fallback(reason: str, root: Optional[Path]) -> Profile:
    logger.error("%s; falling back to %s", reason, DEFAULT_PROFILE)
    found = available_profiles(root)
    if DEFAULT_PROFILE in found:
        source, path = found[DEFAULT_PROFILE]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            profile = parse_profile(data, name=DEFAULT_PROFILE, source=source, path=path)
            profile.fallback_reason = reason
            return profile
        except Exception:
            pass
    return Profile(name=DEFAULT_PROFILE, label="Workstation", fallback_reason=reason)


_cache: dict[str, Profile] = {}


def active_profile(*, refresh: bool = False) -> Profile:
    """The profile for this process, cached per requested name."""
    name = requested_name()
    if refresh or name not in _cache:
        _cache[name] = load_profile(name)
    return _cache[name]


# ─── applying ─────────────────────────────────────────────────────────────────

def plugin_defaults_string(profile: Profile) -> str:
    return ",".join(f"{k}={'true' if v else 'false'}" for k, v in sorted(profile.plugins.items()))


def parse_plugin_defaults(value: Optional[str]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for item in (value or "").split(","):
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        if k.strip():
            out[k.strip()] = _as_bool(v)
    return out


def _effective_env(profile: Profile) -> dict[str, str]:
    """Every env key a profile contributes, including the derived carriers."""
    env = dict(profile.env)
    for key, model in profile.default_models.items():
        if model:
            env[DEFAULT_MODEL_ENV[key]] = model
    if profile.plugins:
        env[PLUGIN_DEFAULTS_ENV] = plugin_defaults_string(profile)
    for key, value in profile.startup.items():
        env[STARTUP_KEYS[key]] = "1" if value else "0"
    return env


def apply_env(profile: Profile, environ: Optional[MutableMapping[str, str]] = None) -> list[str]:
    """Set the profile's env defaults where nothing is set yet. Returns the
    keys it set; an empty list for ``workstation`` is the byte-identical gate."""
    env = os.environ if environ is None else environ
    applied: list[str] = []
    for key, value in _effective_env(profile).items():
        if key not in env:
            env[key] = value
            applied.append(key)
    return applied


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def shell_exports(profile: Profile, environ: Optional[MutableMapping[str, str]] = None) -> list[str]:
    """``export KEY='value'`` lines for ``eval`` in start.sh — only for keys
    the environment does not already carry, so ``.env`` and flags keep winning."""
    env = os.environ if environ is None else environ
    lines = [f"export GUAARDVARK_PROFILE_ACTIVE={_shell_quote(profile.name)}"]
    for key, value in _effective_env(profile).items():
        if key not in env:
            lines.append(f"export {key}={_shell_quote(value)}")
    return lines
