# backend/utils/auth_guard.py
"""Lightweight endpoint protection for dangerous operations.

When GUAARDVARK_API_KEY is set in the environment, protected endpoints
require it from every host, this machine included: in the X-API-Key header
(command-line clients, the MCP server, scripts), or as a browser signed in
with it (the HttpOnly session cookie of backend/utils/api_session.py, which
Settings → Access obtains). When unset, requests from this machine pass and
other hosts are refused. With network access on (GUAARDVARK_NETWORK_ACCESS),
every device on this machine's local network passes as well, key or not,
except on the routes that run a program a caller supplies or names
(RUNS_CODE_PATHS and RUNS_CODE_MUTATION_PREFIXES) and the Interconnector's
operator routes (FLEET_*): those keep the rule above from every other device,
whatever GUAARDVARK_PROTECT_TOOL_ENDPOINTS says. A client also answers its
fleet master's signed request to start a sync (MASTER_SIGNED_PATHS).
/api/auth/ reports the state, signs browsers in and out, and manages the key
and network access.

Agent screen captures (/api/tools/screenshots/) also answer a link signed by
backend/utils/screenshot_urls.py, which is what chat's <img> tags carry.
"""

import os
import hmac
import ipaddress
import logging
import socket

from flask import request, jsonify

logger = logging.getLogger(__name__)

API_KEY_ENV = "GUAARDVARK_API_KEY"
API_KEY_HEADER = "X-API-Key"

# Endpoints that always require protection (any method)
PROTECTED_PREFIXES = (
    # Creating, replacing and removing the API key itself.
    '/api/auth/key',
    # Turning network access on or off.
    '/api/auth/network-access',
    '/api/code-execution/',
    '/api/backups/restore',
    '/api/backups/create',
    '/api/self-code/',
    # Restarting Guaardvark (stops every running job) and the restart log.
    '/api/reboot',
    # Social outreach has kill switches, draft approval, and fetch-meta — none of
    # which should be reachable from another machine on the LAN without an API key.
    '/api/social-outreach/',
    # Connections hold publishing accounts, approve or cancel held publishes,
    # let a Discord webhook post without approval or a YouTube account upload
    # publicly, and rotate the credential store's key. The bare path is the
    # list and create route, so there is no trailing slash.
    '/api/connections',
    # WordPress sites and the pages pulled from them. Registering a site stores
    # its API key or Application Password; pulling and testing send that key
    # to the site.
    '/api/wordpress/',
    # Raw file download for everything under data/outputs, chat exports and
    # screenshots included (backend/routes/download_route.py). Only MCP resource
    # links point here, and those are local; the web UI loads outputs through
    # /api/outputs, which stays open to LAN browsers.
    '/outputs/',
)

# The Interconnector's operator routes: its settings and fleet key, the node
# list, machines joining the fleet, file-change approvals, broadcasts to every
# client, starting a sync, applying master code here, connection probes,
# questions sent on to other machines, and fleet routing. They are protected
# like the routes above, and Network access does not open them to other
# devices (request_controls_fleet()): they answer this machine, and the key
# once there is one, from any address. The routes other machines call (status,
# register, heartbeat, pulls, the manifest, directives) are not listed; each
# checks the calling machine's signature itself (backend/utils/node_auth.py).
FLEET_PREFIXES = (
    '/api/interconnector/config',
    '/api/interconnector/approvals/',
    '/api/interconnector/broadcast/',
    '/api/interconnector/sync/manual',
    '/api/interconnector/ask-family',
    '/api/interconnector/route-inference',
    '/api/interconnector/nodes/test-all',
    '/api/cluster/routing-table/recompute',
    # Enrolling this machine with its master (/enrol), and approving,
    # revoking or removing other machines on the master (/enrolments).
    '/api/interconnector/enrol',
    # Letting machines that joined before enrolment existed sync on the old
    # rule for a while, or closing that window.
    '/api/interconnector/transition',
    # The node list's debug view, probing a master's address, applying
    # master code here, the master's file-scan test and file check, and the
    # list of pre-sync code backups.
    '/api/interconnector/debug/',
    '/api/interconnector/test-connection',
    '/api/interconnector/updates/apply',
    '/api/interconnector/sync/files/test',
    '/api/interconnector/sync/files/verify',
    '/api/interconnector/backups',
)
# Matched whole, any method: the master's list of nodes (hosts and ports).
# /nodes/register, /nodes/<id>/heartbeat and /nodes/local-identity below it
# are node routes and stay open.
FLEET_PATHS = (
    '/api/interconnector/nodes',
)
# Writes only: which entity types this node syncs; reading them stays open.
FLEET_MUTATION_PREFIXES = (
    '/api/interconnector/sync/preferences',
)
# Writes only, (prefix, suffix) around a node id: the master probing one
# client node on the operator's behalf.
FLEET_MUTATION_SUFFIXES = (
    ('/api/interconnector/nodes/', '/test'),
)
# DELETE of the item itself, the node id being the last path segment:
# disconnecting a node on the master.
FLEET_DELETE_ITEM_PREFIXES = (
    '/api/interconnector/nodes/',
)

# Protected routes the fleet's master may also call on a client, with a
# request signed by the master's own key (backend/utils/node_auth.py): a
# broadcast starts a sync here.
MASTER_SIGNED_PATHS = (
    '/api/interconnector/sync/manual',
)

# Browser/desktop/MCP automation and direct tool execution can read files, run
# commands and reach internal networks, and a call to /api/tools/execute skips
# the confirmation prompts chat would show. Tool jobs hold the results of those
# calls. These routes answer only this machine, or a caller that sends the API
# key; a browser on another device is signed in once the key is entered in
# Settings → Access.
# GUAARDVARK_PROTECT_TOOL_ENDPOINTS=false (or 0, no, off) opens them to every
# host that can reach the backend; any other value, or none, keeps them closed.
# It is read per request, like GUAARDVARK_API_KEY.
TOOL_ENDPOINTS_ENV = "GUAARDVARK_PROTECT_TOOL_ENDPOINTS"
TOOL_ENDPOINT_PREFIXES = (
    '/api/automation/',
    '/api/tools/jobs/',
)
# Matched whole: GET /api/tools/execute_python is a tool's schema, not a call.
TOOL_ENDPOINT_PATHS = (
    '/api/tools/execute',
)

# The refusals. Each carries a code the web UI recognises (it then words the
# advice for the page it is on and links to Settings → Access); the CLI shows
# the text as it is, so the text says what to do in both places.
# local_only: this install has no key and the caller is another host.
# api_key_required: this install has a key and the caller did not send it.
# credential_rejected in the body: the caller did send a key or a sign-in, and
# it is not accepted now.
LOCAL_ONLY_CODE = "local_only"
# outside_local_network: network access is on, no key, and the caller is not
# on the local network.
OUTSIDE_NETWORK_CODE = "outside_local_network"
# runs_code_key_required: network access is on, no key, and the route runs a
# program, which network access does not open to other devices.
RUNS_CODE_CODE = "runs_code_key_required"
# fleet_key_required: network access is on, no key, and the route controls the
# Interconnector fleet, which network access does not open to other devices.
FLEET_CODE = "fleet_key_required"
API_KEY_CODE = "api_key_required"
SCREENSHOT_LINK_CODE = "screenshot_link_invalid"
LOCAL_ONLY_MESSAGE = (
    "This action works only on {machine} itself: network access is off and "
    "this install has no API key. To use it from another device, turn on "
    "Settings → Access → Network access on {machine}, or create an API key "
    "there and enter it on that device; command-line and API clients send "
    "it in the X-API-Key header."
)
OUTSIDE_NETWORK_MESSAGE = (
    "This action works only on {machine} and on devices on its local network, "
    "and this device is outside it. Create an API key in Settings → Access on "
    "{machine} and enter it on this device; command-line and API clients send "
    "it in the X-API-Key header."
)
RUNS_CODE_MESSAGE = (
    "This action runs a program on {machine}, which works only on {machine} "
    "itself or with its API key, even while Network access is on. Create an "
    "API key in Settings → Access on {machine} and enter it on this device; "
    "command-line and API clients send it in the X-API-Key header."
)
FLEET_MESSAGE = (
    "This Interconnector action works only on {machine} itself or with its "
    "API key, even while Network access is on. Create an API key in "
    "Settings → Access on {machine} and enter it on this device; command-line "
    "and API clients send it in the X-API-Key header."
)
API_KEY_MESSAGE = (
    "This action needs {machine}'s API key. In the web UI, enter it in "
    "Settings → Access; command-line and API clients send it in the "
    "X-API-Key header (GUAARDVARK_API_KEY)."
)
# Says nothing about whether the file exists.
SCREENSHOT_LINK_MESSAGE = (
    "This screenshot link is not valid for this install. Open the screenshot "
    "from the chat it appeared in."
)
SCREENSHOT_PREFIX = "/api/tools/screenshots/"


def tool_endpoints_protected() -> bool:
    """True unless GUAARDVARK_PROTECT_TOOL_ENDPOINTS opts out."""
    return os.environ.get(TOOL_ENDPOINTS_ENV, "").strip().lower() not in ("0", "false", "no", "off")


# Settings → Access → Network access. On (1, true, yes, on), every protected
# action is open to this machine, with or without the API key, and to every
# device on its local network, except the routes that run a program
# (RUNS_CODE_* below) and the Interconnector's operator routes (FLEET_* above);
# an address outside the local network still needs the key. Off or unset keeps
# the rules above. Read per request, like GUAARDVARK_API_KEY;
# backend/services/network_access_service.py changes it.
NETWORK_ACCESS_ENV = "GUAARDVARK_NETWORK_ACCESS"


def network_access_open() -> bool:
    """True when network access is on."""
    return os.environ.get(NETWORK_ACCESS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


# Routes that run a program the caller supplies or names, as the user
# Guaardvark runs as, with no sandbox:
# - the Code Editor's Run (python, javascript, shell, build); format and lint
#   only run a formatter or linter over the text and stay ordinary protected
#   routes;
# - direct tool execution, where the caller stands in for the approval card
#   chat would show: execute_python and system_command on /api/tools/execute,
#   policy-gated MCP tools on /api/automation/mcp/execute;
# - MCP server entries, which name a program the backend starts (any command,
#   no allow-list), and connecting or reloading them;
# - approving and applying a staged self-improvement fix, which writes code
#   into this install that runs at the next restart.
# Network access does not open these to other devices, and the
# GUAARDVARK_PROTECT_TOOL_ENDPOINTS opt-out does not open them to other hosts:
# they answer this machine, and the key once there is one, from any address.
# The paths are matched whole: GET /api/tools/execute_python is a tool's
# schema, not a call. The prefixes apply to writes only: GET on a server entry
# or a staged fix reads it.
RUNS_CODE_PATHS = (
    '/api/code-execution/python',
    '/api/code-execution/javascript',
    '/api/code-execution/shell',
    '/api/code-execution/build',
    '/api/tools/execute',
    '/api/automation/mcp/execute',
    '/api/automation/mcp/connect',
    '/api/automation/mcp/reload-config',
)
RUNS_CODE_MUTATION_PREFIXES = (
    '/api/automation/mcp/servers/',
    '/api/self-improvement/pending-fixes/',
)


def request_runs_code() -> bool:
    """True when the current request targets a route that runs a supplied or
    named program (the lists above)."""
    path = request.path
    if path in RUNS_CODE_PATHS:
        return True
    return request.method not in ('GET', 'HEAD', 'OPTIONS') and path.startswith(RUNS_CODE_MUTATION_PREFIXES)


def request_controls_fleet() -> bool:
    """True when the current request targets an Interconnector operator route
    (the FLEET_* lists above)."""
    path = request.path
    if path.startswith(FLEET_PREFIXES) or (path.rstrip('/') or '/') in FLEET_PATHS:
        return True
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return False
    if path.startswith(FLEET_MUTATION_PREFIXES):
        return True
    for prefix, suffix in FLEET_MUTATION_SUFFIXES:
        if path.startswith(prefix) and path.rstrip('/').endswith(suffix):
            return True
    if request.method == 'DELETE':
        for prefix in FLEET_DELETE_ITEM_PREFIXES:
            if path.startswith(prefix) and '/' not in path[len(prefix):].strip('/'):
                return True
    return False


def network_access_covers_request() -> bool:
    """True when network access is on and opens the current request to the
    caller: this machine on every protected route (as it was before the
    routes that run code were set apart), and a local-network address on any
    protected route but those and the Interconnector's operator routes."""
    if not network_access_open():
        return False
    if request_is_from_this_machine():
        return True
    return (request_is_from_local_network() and not request_runs_code()
            and not request_controls_fleet())


def machine_name() -> str:
    """This machine's host name, for messages that send someone to it."""
    try:
        return socket.gethostname() or "the Guaardvark machine"
    except OSError:
        return "the Guaardvark machine"


# File APIs include both the document library and the live repository editor.
# Keep read-only document browser GETs public for the local UI, but protect
# server filesystem reads and every mutation-capable file route.
PROTECTED_FILE_PREFIXES = (
    '/api/files/read',
    '/api/files/list',
    '/api/files/write',
    '/api/files/create',
    '/api/files/delete',
    '/api/files/mkdir',
    '/api/files/rename',
    '/api/files/browse-server',
    # Reads files anywhere on the server to describe a training dataset.
    '/api/training/datasets/',
    # Reads a folder of notes for a training dataset; answers with file paths and scrubbed lines.
    '/api/training/pipeline/build-from-folder',
    # The private word list a folder build scrubs (names of people, machines, clients).
    '/api/training/scrub-terms',
)

# Endpoints protected only on DELETE
PROTECTED_DELETE_PREFIXES = (
    '/api/backups/',
    # Cast subjects, reference images and samples; generate/train stay LAN-usable.
    '/api/cast-library/',
    # Clearing the audio job history; generation and cancel stay LAN-usable.
    '/api/audio-foundry/jobs',
)

# Protected only on DELETE of the item itself, the id being the last path
# segment. Deleting an imported voice clip removes a person's recording, like
# the Cast Library deletes above. Withdrawing consent for a clip
# (DELETE .../voice-clips/<id>/consent) stays as open as recording it
# (POST .../consent and the import), so withdrawing is never harder than giving.
PROTECTED_DELETE_ITEM_PREFIXES = (
    '/api/audio-foundry/voice-clips/',
)

# Explicitly safe operations that are exempt from the host check even though they
# live under an otherwise-protected prefix. /api/meta is shared by many blueprints
# (jobs, index management, diagnostics) that MUST stay protected, but clearing
# __pycache__ is a non-destructive maintenance op (regenerable .pyc only; the
# module-purge that could destabilize the server is disabled) that the operator
# wants reachable from the LAN UI. Keep this list tiny and genuinely harmless.
SAFE_EXEMPT_PREFIXES = (
    '/api/meta/clear-pycache',
)

# Mutation-only protection: GET/HEAD/OPTIONS stay public for the local UI, but any
# non-GET (create/cancel/delete/run) requires auth/localhost — same model as the
# /api/memory hardening. Stops a random LAN host from wiping jobs/tasks/schedules.
MUTATION_PROTECTED_PREFIXES = (
    # Writing an MCP server entry names a program the backend will start, so
    # config changes are never open to other hosts.
    '/api/automation/mcp/servers/',
    '/api/automation/mcp/reload-config',
    # Turning the project-folder limit off widens what tools may read.
    '/api/settings/confine_tool_paths',
    # Switching the inbound guard off, or approving a change it held, lets code in.
    '/api/settings/inbound_guard',
    # Approving or applying a staged fix writes code into the checkout.
    '/api/self-improvement/pending-fixes',
    # Every Uncle Claude POST sends to Anthropic or changes when it may
    # (escalation mode, scheduled sends); the status stays readable.
    '/api/claude/',
    # Persists the product profile into .env.
    '/api/settings/profile',
    '/api/memory',
    '/api/tasks',
    '/api/scheduler',
    '/api/jobs',
    '/api/meta',
    '/api/progress-test',
    # Destructive batch-image file operations (delete/rename/move). Generation
    # and reads stay reachable from LAN browsers, which have no API-key field.
    '/api/batch-image/image/',
    '/api/batch-image/delete/',
    '/api/batch-image/rename/',
    '/api/batch-image/move/',
    '/api/batch-image/folder',
    # GPU control (stop Ollama, force-release leases, evict) and upscaling jobs.
    '/api/gpu',
    '/api/upscaling',
    # Installing or removing the training libraries runs pip in the backend's
    # Python environment; their status stays readable.
    '/api/training/libraries/',
    # Downloading or deleting a training base model; the list stays readable.
    '/api/training/base-models/',
)

# Mutation-only protection for routes whose id sits mid-path: (prefix, suffix).
# Importing a Cast LoRA writes a file of up to a few GB and replaces the member's
# LoRA, so it is closed to other hosts; train/generate/upload-refs stay LAN-usable.
MUTATION_PROTECTED_SUFFIXES = (
    ('/api/cast-library/subjects/', '/import-lora'),
)


def _normalize_ip(addr: str) -> str:
    """Normalize IP for localhost checks (handles IPv4-mapped IPv6 like ::ffff:127.0.0.1 and zone IDs)."""
    if not addr:
        return ""
    a = addr.strip()
    if a.lower().startswith("::ffff:"):
        a = a[7:]
    if "%" in a:
        a = a.split("%", 1)[0]
    return a.lower()


_local_ips_cache: set[str] | None = None


def _get_local_ips() -> set[str]:
    """Return IPs that belong to this machine.

    Used so that when the operator loads the UI via a LAN IP/hostname (instead of
    pure localhost), requests proxied through our Vite server are still treated as
    "the person at the console" for no-API-key sensitive endpoints (backups/create,
    self-code, etc.). Different LAN devices get their own IPs in X-Forwarded-For and
    remain blocked.
    """
    global _local_ips_cache
    if _local_ips_cache is not None:
        return _local_ips_cache

    ips: set[str] = {"127.0.0.1", "::1", "localhost"}
    try:
        import socket

        # Primary outbound IP (the one the box uses to talk to the world / LAN gateway)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            src = s.getsockname()[0]
            if src:
                ips.add(src.lower())
        finally:
            s.close()
    except Exception:
        pass

    _local_ips_cache = ips
    return ips


def _is_localhost(addr: str) -> bool:
    """Check if address is loopback or one of this machine's own IPs (incl. LAN IP).

    This makes "access via LAN URL on the operator's own machine" work for no-key
    sensitive ops while still blocking *other* LAN hosts.
    """
    if not addr:
        return False
    a = _normalize_ip(addr)
    if a in ("127.0.0.1", "::1", "localhost"):
        return True
    return a in _get_local_ips()


def _effective_client_ip():
    """Real client IP, accounting for the trusted local Vite proxy.

    `start.sh` serves the production UI via `vite preview`, whose proxy forwards
    /api and /socket.io to Flask from 127.0.0.1 — so a LAN device's request would
    otherwise look local and bypass this guard entirely. The Vite proxy sets
    X-Forwarded-For (xfwd) with the originating client. We trust that header ONLY
    when the direct TCP peer is loopback (i.e. it came through our own local
    proxy). A LAN attacker connecting straight to the backend port has a
    non-loopback peer, so a forged X-Forwarded-For from them is ignored.

    We now also use a robust _is_localhost check (with own-machine-IP detection)
    so an operator reaching the UI via their own LAN IP/hostname still gets
    full localhost-like access for sensitive ops when no GUAARDVARK_API_KEY.
    """
    peer = request.remote_addr or ""
    if _is_localhost(peer):
        xff = request.headers.get('X-Forwarded-For', '')
        if xff:
            # Rightmost entry: the proxy appends the address it saw to whatever
            # the client sent, so earlier entries are the client's own claim.
            return xff.split(',')[-1].strip()
    return peer


def _is_preflight_flask_answers() -> bool:
    """An OPTIONS request that Flask answers itself, without running a view.

    That is a browser's CORS preflight: it never carries a key or a cookie,
    so refusing it only makes the browser drop the real request that would
    carry them. Flask-CORS adds the CORS headers to Flask's answer for this
    install's own origins only. An OPTIONS to a route whose view handles
    OPTIONS itself is guarded like any other request.
    """
    if request.method != "OPTIONS":
        return False
    rule = request.url_rule
    # No rule: Flask answers 404 or 405 and no view runs.
    return rule is None or bool(getattr(rule, "provide_automatic_options", False))


def _is_protected():
    """Check if the current request targets a protected endpoint."""
    path = request.path
    for prefix in SAFE_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return False
    # Before the tool-endpoint opt-out: it never opens a route that runs code.
    if request_runs_code() or request_controls_fleet():
        return True
    for prefix in PROTECTED_PREFIXES:
        if path.startswith(prefix):
            return True
    if (path in TOOL_ENDPOINT_PATHS or path.startswith(TOOL_ENDPOINT_PREFIXES)) and tool_endpoints_protected():
        return True
    for prefix in PROTECTED_FILE_PREFIXES:
        if path.startswith(prefix):
            return True
    if path.startswith('/api/files/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return True
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        for prefix in MUTATION_PROTECTED_PREFIXES:
            if path.startswith(prefix):
                return True
        for prefix, suffix in MUTATION_PROTECTED_SUFFIXES:
            if path.startswith(prefix) and path.rstrip('/').endswith(suffix):
                return True
    if request.method == 'DELETE':
        for prefix in PROTECTED_DELETE_PREFIXES:
            if path.startswith(prefix):
                return True
        for prefix in PROTECTED_DELETE_ITEM_PREFIXES:
            if path.startswith(prefix) and '/' not in path[len(prefix):].strip('/'):
                return True
    return False


def configured_api_key() -> str:
    """This install's API key as the running process has it, or ""."""
    return (os.environ.get(API_KEY_ENV) or "").strip()


def request_carries_valid_key() -> bool:
    """True when a key is configured and the request sent that key."""
    api_key = configured_api_key()
    provided = request.headers.get(API_KEY_HEADER, "")
    if not api_key or not provided:
        return False
    # Bytes, so a header with non-ASCII characters compares unequal instead of
    # raising.
    return hmac.compare_digest(provided.encode("utf-8"), api_key.encode("utf-8"))


def request_is_from_this_machine() -> bool:
    """True for the Guaardvark machine itself, through the local proxy or not."""
    # The effective client IP, so a LAN device proxied through the local Vite
    # preview is still treated as remote (the proxy makes request.remote_addr
    # loopback otherwise).
    return _is_localhost(_effective_client_ip())


def request_is_from_local_network() -> bool:
    """True for this machine and for private, loopback and link-local
    addresses: the devices network access opens to. A public address, or one
    in shared address space, as some VPNs hand out, is not."""
    addr = _effective_client_ip()
    if _is_localhost(addr):
        return True
    try:
        ip = ipaddress.ip_address(_normalize_ip(addr))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def request_has_valid_session() -> bool:
    """True when a browser signed in with the current key sent its cookie."""
    from backend.utils.api_session import VALID, session_state

    return session_state() == VALID


def credential_rejected() -> bool:
    """True when the request carried a key or a sign-in that is not accepted
    now (a wrong key, or a browser signed in with a key since replaced or
    removed). The web UI words its advice differently for that case."""
    from backend.utils.api_session import REJECTED, session_state

    sent_key = bool(request.headers.get(API_KEY_HEADER)) and not request_carries_valid_key()
    return sent_key or session_state() == REJECTED


def caller_is_authorized() -> bool:
    """The rule every protected route applies: any device on the local network
    while network access is on, except on a route that runs code or controls
    the Interconnector fleet; otherwise the key (header or signed-in browser)
    once one is configured, this machine until then."""
    if network_access_covers_request():
        return True
    if configured_api_key():
        return request_carries_valid_key() or request_has_valid_session()
    return request_is_from_this_machine()


def _signed_by_fleet_master() -> bool:
    """True when the fleet's master signed this request with its own key."""
    # node_auth.HEADER_SIG, checked before importing it for unsigned requests.
    if not request.headers.get("X-GX-Sig"):
        return False
    from backend.utils.node_auth import flask_request_signed_by_master

    return flask_request_signed_by_master()


def _screenshot_link_is_signed() -> bool:
    from backend.utils.screenshot_urls import SIGNATURE_PARAM, signature_valid

    rel_path = request.path[len(SCREENSHOT_PREFIX):]
    return signature_valid(rel_path, request.args.get(SIGNATURE_PARAM))


def protected_summary() -> list[str]:
    """What needs this machine or the key, in words for the Settings page."""
    items = []
    if tool_endpoints_protected():
        items += [
            "Running tools directly (Tools page) and their jobs",
            "Automation and MCP servers",
        ]
    else:
        items += [
            "Running tools directly (Tools page)",
            "Changing, connecting and running tools on MCP servers",
        ]
    items += [
        "Code execution",
        "Restarting Guaardvark",
        "Creating, restoring and deleting backups",
        "Editing files and browsing the server's folders",
        "Reading Guaardvark's own source (self-code)",
        "Social outreach",
        "Connections and publishing",
        "WordPress sites and their pages",
        "Interconnector settings, the node list, machines joining the fleet, approvals, "
        "broadcasts, applying master updates and fleet routing",
        "Changing tasks, jobs, schedules, memory and GPU state",
        "Raw file downloads under /outputs/",
        "Managing this API key",
    ]
    return items


def _refusal(message: str, code: str, status: int):
    machine = machine_name()
    return jsonify({
        "error": message.format(machine=machine),
        "code": code,
        "credential_rejected": credential_rejected(),
        "machine": machine,
    }), status


def check_endpoint_auth():
    """Flask before_request hook: enforce auth on dangerous endpoints.

    Logic:
    - An OPTIONS request Flask answers itself (a CORS preflight) → allow
    - Agent screen captures: a link signed for that path passes, then the rule below
    - If endpoint is not protected → allow
    - A client's sync started by its fleet master's signed request → allow
    - If network access is on → allow this machine, and the local network
      except on a route that runs a program or controls the Interconnector
      fleet (the rules below apply there)
    - If GUAARDVARK_API_KEY is set → require X-API-Key header (any host)
    - If GUAARDVARK_API_KEY is NOT set → allow localhost, block remote
    """
    if _is_preflight_flask_answers():
        return None

    if request.path.startswith(SCREENSHOT_PREFIX):
        if _screenshot_link_is_signed() or caller_is_authorized():
            return None
        logger.warning(
            f"[AUTH] Refused unsigned screenshot link {request.path} from {_effective_client_ip()}"
        )
        # Refused before the route runs, so the answer is the same whether or
        # not the file exists.
        return _refusal(SCREENSHOT_LINK_MESSAGE, SCREENSHOT_LINK_CODE, 403)

    if not _is_protected():
        return None

    if request.path.rstrip('/') in MASTER_SIGNED_PATHS and _signed_by_fleet_master():
        return None

    if network_access_covers_request():
        return None

    if not configured_api_key():
        if request_is_from_this_machine():
            return None
        logger.warning(
            f"[AUTH] Blocked remote access to {request.path} from {_effective_client_ip()}"
        )
        if network_access_open():
            if request_runs_code():
                return _refusal(RUNS_CODE_MESSAGE, RUNS_CODE_CODE, 403)
            if request_controls_fleet():
                return _refusal(FLEET_MESSAGE, FLEET_CODE, 403)
            return _refusal(OUTSIDE_NETWORK_MESSAGE, OUTSIDE_NETWORK_CODE, 403)
        return _refusal(LOCAL_ONLY_MESSAGE, LOCAL_ONLY_CODE, 403)

    if request_carries_valid_key() or request_has_valid_session():
        return None

    logger.warning(
        f"[AUTH] Invalid/missing API key or sign-in for {request.path} from {request.remote_addr}"
    )
    return _refusal(API_KEY_MESSAGE, API_KEY_CODE, 401)
