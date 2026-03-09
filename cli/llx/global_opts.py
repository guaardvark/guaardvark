"""Global CLI options — set by root callback, read by commands."""

_global_server: str | None = None
_global_json: bool = False
_global_timeout: float | None = None
_global_verbose: bool = False
_global_quiet: bool = False


def set_global_opts(
    server: str | None = None,
    json_out: bool = False,
    timeout: float | None = None,
    verbose: bool = False,
    quiet: bool = False,
):
    """Set global options from root callback."""
    global _global_server, _global_json, _global_timeout, _global_verbose, _global_quiet
    _global_server = server
    _global_json = json_out
    _global_timeout = timeout
    _global_verbose = verbose
    _global_quiet = quiet


def get_global_server() -> str | None:
    """Get server URL override from global options."""
    return _global_server


def get_global_json() -> bool:
    """Get JSON output flag from global options."""
    return _global_json


def get_global_timeout() -> float | None:
    """Get timeout override from global options."""
    return _global_timeout


def get_global_verbose() -> bool:
    """Get verbose flag from global options."""
    return _global_verbose


def get_global_quiet() -> bool:
    """Get quiet flag from global options."""
    return _global_quiet
