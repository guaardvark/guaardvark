"""CLI for profiles — used by start.sh before the venv exists, so it must run on
any Python 3 with the standard library alone.

    python backend/profiles/__main__.py export --shell     # eval-able exports
    python backend/profiles/__main__.py show [name]        # resolved profile as JSON
    python backend/profiles/__main__.py list

Loaded by file path rather than as ``backend.profiles`` because importing the
``backend`` package pulls in Socket.IO and the rest of the app.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    try:
        from backend import profiles  # normal case inside the app
        return profiles
    except Exception:
        spec = importlib.util.spec_from_file_location(
            "guaardvark_profiles", Path(__file__).with_name("__init__.py")
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules
        spec.loader.exec_module(mod)
        return mod


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="profiles")
    sub = p.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export", help="print profile defaults for the shell")
    exp.add_argument("--shell", action="store_true", help="emit `export KEY='value'` lines")
    exp.add_argument("name", nargs="?")
    show = sub.add_parser("show", help="print the resolved profile")
    show.add_argument("name", nargs="?")
    sub.add_parser("list", help="list available profiles")
    args = p.parse_args(argv)

    profiles = _load_module()
    if args.cmd == "list":
        for name, (source, path) in profiles.available_profiles().items():
            print(f"{name}\t{source}\t{path}")
        return 0
    profile = profiles.load_profile(args.name)
    if args.cmd == "show":
        out = profile.public_dict()
        out.update({"env": profile.env, "plugins": profile.plugins, "startup": profile.startup,
                    "default_models": profile.default_models, "path": str(profile.path or ""),
                    "warnings": profile.warnings})
        print(json.dumps(out, indent=2))
        return 0
    if args.shell:
        print("\n".join(profiles.shell_exports(profile)))
    else:
        print(json.dumps(profiles._effective_env(profile), indent=2))
    if profile.fallback_reason:
        print(f"# WARNING: {profile.fallback_reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
