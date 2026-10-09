"""What this install exposes and what guards it, read from the running state.

GET /api/settings/security/check answers with this. Every item is read, not
assumed: the API key from the running process (never the key itself), the
guards from backend/utils/auth_guard.py and backend/utils/cors_policy.py, the
web-access and tool-path settings from the database, and which addresses
each service listens on from the operating system's socket table. Nothing is
started, stopped or contacted.

A check is "ok", "info" (a fact worth knowing, as designed) or "warn" (a
setting or exposure that weakens a guard). The overall level counts warns.

The backend and the web UI listen on every interface by default so other
devices on the network can use Guaardvark; that is reported as info with what
it means for this install's API key. A service other than those two that is
reachable from the network is a warn: a caller there reaches it directly,
without Guaardvark's API key.
"""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit

OK, INFO, WARN = "ok", "info", "warn"

_ROOT = Path(__file__).resolve().parents[2]

# Where a sidecar's listen address is set, for the advice on a warn. Only
# entries checked against each plugin's scripts/start.sh; others fall back to
# naming that script.
_BIND_SETTING = {
    "audio_foundry": "GUAARDVARK_AUDIO_FOUNDRY_HOST",
    "comfyui": "GUAARDVARK_COMFYUI_LISTEN",
    "upscaling": "GUAARDVARK_UPSCALING_HOST",
    "swarm": "SWARM_BIND_HOST",
    "ollama": "OLLAMA_HOST",
}

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _check(check_id: str, title: str, status: str, detail: str, **extra) -> dict:
    return {"id": check_id, "title": title, "status": status, "detail": detail, **extra}


# --- Listening sockets -------------------------------------------------------

def listening_addresses() -> Optional[dict[int, set[str]]]:
    """{port: {address, ...}} for every TCP socket in LISTEN, or None when the
    socket table cannot be read."""
    try:
        import psutil

        connections = psutil.net_connections(kind="tcp")
    except Exception:  # noqa: BLE001 - reported as "could not read"
        return None
    table: dict[int, set[str]] = {}
    for conn in connections:
        if conn.status == "LISTEN" and conn.laddr:
            table.setdefault(conn.laddr.port, set()).add(conn.laddr.ip)
    return table


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def exposure(addresses: Iterable[str]) -> str:
    """"none" (not listening), "loopback" (this machine only) or "network"."""
    addresses = list(addresses)
    if not addresses:
        return "none"
    return "loopback" if all(_is_loopback(a) for a in addresses) else "network"


def _shown(addresses: Iterable[str]) -> list[str]:
    return sorted(f"[{a}]" if ":" in a else a for a in addresses)


# --- What should be listening -------------------------------------------------

def _url_port(url: Optional[str], default: int) -> Optional[int]:
    """The port of a URL whose host is this machine; None for another host."""
    if not url:
        return default
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if host not in _LOCAL_HOSTS:
        return None
    return port or default


def _plugin_ports() -> list[tuple[str, str, int]]:
    """(plugin id, display name, port) from each plugins/<id>/plugin.json."""
    found = []
    for manifest in sorted((_ROOT / "plugins").glob("*/plugin.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            port = int(data.get("port"))
        except (OSError, ValueError, TypeError):
            continue
        plugin_id = manifest.parent.name
        found.append((plugin_id, data.get("name") or plugin_id, port))
    return found


def _exposure_checks(table: Optional[dict[int, set[str]]], api_key_set: bool) -> list[dict]:
    if table is None:
        return [_check(
            "listening", "Listening services", INFO,
            "This system did not let Guaardvark read its socket table, so which "
            "addresses each service listens on is not shown.",
        )]

    from backend.utils.cors_policy import flask_port, vite_port

    checks = []
    for check_id, title, port_text in (("backend", "Backend API", flask_port()), ("frontend", "Web UI", vite_port())):
        try:
            port = int(port_text)
        except ValueError:
            continue
        addresses = table.get(port, set())
        reach = exposure(addresses)
        if reach == "network":
            who = (
                "protected actions need the API key, from any device"
                if api_key_set else
                "protected actions answer only this machine; there is no API key yet"
            )
            detail = (
                f"Listens on {', '.join(_shown(addresses))} port {port}, so other devices on the "
                f"network can open it: {who}, and the rest (chat, generation, documents) "
                "answers any device that reaches it."
            )
            if check_id == "backend":
                detail += " FLASK_RUN_HOST=127.0.0.1 keeps it to this machine."
            checks.append(_check(check_id, title, INFO, detail, port=port, addresses=_shown(addresses)))
        elif reach == "loopback":
            checks.append(_check(check_id, title, OK, f"Listens on this machine only (port {port}).",
                                 port=port, addresses=_shown(addresses)))
        else:
            checks.append(_check(check_id, title, INFO, f"Nothing listens on port {port}.", port=port, addresses=[]))

    services = [
        ("redis", "Redis (task queue)", _url_port(os.environ.get("CELERY_BROKER_URL"), 6379),
         "Redis's own bind setting"),
        ("postgres", "PostgreSQL (database)", _url_port(os.environ.get("DATABASE_URL"), 5432),
         "PostgreSQL's listen_addresses"),
    ]
    for plugin_id, name, port in _plugin_ports():
        setting = _BIND_SETTING.get(plugin_id)
        where = setting or f"plugins/{plugin_id}/scripts/start.sh"
        services.append((f"plugin:{plugin_id}", name, port, where))

    for check_id, title, port, where in services:
        if port is None:
            continue
        addresses = table.get(port, set())
        reach = exposure(addresses)
        if reach == "network":
            checks.append(_check(
                check_id, title, WARN,
                f"Listens on {', '.join(_shown(addresses))} port {port}: other machines on the "
                f"network reach it directly, without Guaardvark's API key. To keep it to this "
                f"machine, bind it to 127.0.0.1 ({where}).",
                port=port, addresses=_shown(addresses),
            ))
        elif reach == "loopback":
            checks.append(_check(check_id, title, OK, f"Listens on this machine only (port {port}).",
                                 port=port, addresses=_shown(addresses)))
        else:
            checks.append(_check(check_id, title, INFO, f"Not running (nothing listens on port {port}).",
                                 port=port, addresses=[]))
    return checks


# --- Guards and settings -------------------------------------------------------

def _guard_checks(debug: bool) -> tuple[list[dict], bool]:
    from backend.services import api_key_service
    from backend.utils import auth_guard, cors_policy

    checks = []
    state = api_key_service.key_state()
    if state.configured:
        checks.append(_check(
            "api_key", "API key", OK,
            "Set. Protected actions (code execution, backups, restarts, file editing, "
            "outreach and the rest Settings → Access lists) need it from every device, "
            "this one included.",
        ))
    else:
        checks.append(_check(
            "api_key", "API key", INFO,
            "Not set. Protected actions answer only this machine; other devices cannot run "
            "them until a key is created in Settings → Access.",
        ))
    if state.restart_needed:
        checks.append(_check(
            "api_key_restart", "API key not loaded", WARN,
            "The key saved in .env is not the one the running backend uses. Restart "
            "Guaardvark to apply it.",
        ))

    if auth_guard.network_access_open():
        checks.append(_check(
            "network_access", "Network access", WARN,
            "On. Every device on this machine's local network can run protected actions "
            "without the API key. Turn it off in Settings → Access on a network you do "
            "not trust.",
        ))

    if auth_guard.tool_endpoints_protected():
        checks.append(_check(
            "tool_endpoints", "Tool and automation endpoints", OK,
            "Protected: running tools directly, tool jobs, and browser/desktop automation "
            "need this machine or the API key.",
        ))
    else:
        checks.append(_check(
            "tool_endpoints", "Tool and automation endpoints", WARN,
            f"Open to every host that reaches the backend ({auth_guard.TOOL_ENDPOINTS_ENV} is "
            "off). These run commands, read files and drive the browser and desktop without "
            "the confirmations chat shows.",
        ))

    if cors_policy.any_host_allowed():
        checks.append(_check(
            "host_check", "Host name check", WARN,
            "Off (VITE_ALLOWED_HOSTS=all): the backend answers requests addressed to any "
            "name, which is what lets a web page re-point its own name at this machine "
            "(DNS rebinding) and use the API.",
        ))
    else:
        checks.append(_check(
            "host_check", "Host name check", OK,
            "On: requests addressed to a name this install does not use are refused.",
        ))

    extra = cors_policy.extra_origins()
    if extra:
        checks.append(_check(
            "cors_origins", "Extra web origins", INFO,
            f"{cors_policy.EXTRA_ORIGINS_ENV} lets {len(extra)} other origin(s) call the API "
            "from a browser and send state-changing requests. Keep only frontends you serve.",
        ))
    else:
        checks.append(_check(
            "cors_origins", "Extra web origins", OK,
            "None: only this install's own web UI can call the API from a browser.",
        ))

    if debug:
        checks.append(_check(
            "debug", "Debug mode", WARN,
            "On (FLASK_DEBUG): error pages show tracebacks and the interactive debugger to "
            "whoever reaches the backend.",
        ))
    else:
        checks.append(_check("debug", "Debug mode", OK, "Off."))
    return checks, state.configured


def _setting_checks() -> list[dict]:
    from backend.utils.settings_utils import get_confine_tool_paths, get_web_access

    checks = []
    if get_web_access():
        checks.append(_check(
            "web_access", "Web access", INFO,
            "On (Settings): web search and page fetching are allowed, which sends those "
            "queries and page addresses out from this machine.",
        ))
    else:
        checks.append(_check(
            "web_access", "Web access", OK,
            "Off (Settings): web search and page fetching are refused.",
        ))
    if get_confine_tool_paths():
        checks.append(_check(
            "tool_paths", "Tool file access", OK,
            "Limited to the project folder and GUAARDVARK_ALLOWED_PATHS.",
        ))
    else:
        checks.append(_check(
            "tool_paths", "Tool file access", INFO,
            "Not limited to the project folder. Settings can limit file-reading tools to "
            "it and GUAARDVARK_ALLOWED_PATHS.",
        ))
    try:
        from backend.services.inbound_guard_posture import _outbound_titles, snap_outbound

        state = snap_outbound()
        on = [(_outbound_titles.get(k) or {}).get("title", k) for k, v in state.items() if v == "on"]
        checks.append(_check(
            "outbound_paths", "Outbound paths", INFO if on else OK,
            ("On: " + "; ".join(on) + ". The full list, with what each sends, is "
             "scripts/inbound_guard/egress.json.") if on else
            "None of the switchable outbound paths is on; downloads and installs still run when you start them.",
        ))
    except Exception as exc:  # the security check must answer even if this cannot
        checks.append(_check("outbound_paths", "Outbound paths", INFO, f"Could not read them: {exc}"))

    from backend.services.inbound_guard_service import get_mode

    mode = get_mode()
    if mode == "enforce":
        checks.append(_check(
            "inbound_guard", "Inbound guard", OK,
            "Enforcing: code the product writes into its own checkout is read first, and risky "
            "changes wait for approval.",
        ))
    elif mode == "observe":
        checks.append(_check(
            "inbound_guard", "Inbound guard", INFO,
            "Observing: code the product writes into its own checkout is read and recorded, "
            "but nothing is held.",
        ))
    else:
        checks.append(_check(
            "inbound_guard", "Inbound guard", INFO,
            "Off. Settings can have the product read code before it writes it into its own "
            "checkout, and hold risky changes for approval.",
        ))
    return checks


def security_summary(debug: bool = False) -> dict:
    """Every check, the warnings among them, and an overall level."""
    guards, api_key_set = _guard_checks(debug)
    checks = guards + _setting_checks() + _exposure_checks(listening_addresses(), api_key_set)
    warnings = [f"{c['title']}: {c['detail']}" for c in checks if c["status"] == WARN]
    level = "high" if not warnings else "medium" if len(warnings) < 3 else "low"
    return {
        "checks": checks,
        "warnings": warnings,
        "warning_count": len(warnings),
        "security_level": level,
    }
