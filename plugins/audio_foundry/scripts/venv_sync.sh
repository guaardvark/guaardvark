#!/bin/bash
# plugins/audio_foundry/scripts/venv_sync.sh
# Sourced by setup_venv.sh and start.sh: one definition of how the main
# Audio Foundry venv is filled and of what "up to date" means for it.

# af_requirements_ok <venv> <requirements file>...
# 0 when every requirement listed in the files is installed at a version the
# file allows. Reads package metadata only; installs nothing.
af_requirements_ok() {
    local venv="$1"; shift
    [ -x "$venv/bin/python" ] || return 1
    "$venv/bin/python" - "$@" <<'PYEOF'
import sys
from importlib import metadata

try:
    from packaging.requirements import Requirement
except Exception as e:
    print(f"REQUIREMENTS_FAIL: packaging not importable ({e!r})", file=sys.stderr)
    sys.exit(1)

bad = []
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            req = Requirement(line)
            if req.marker is not None and not req.marker.evaluate():
                continue
            try:
                have = metadata.version(req.name)
            except metadata.PackageNotFoundError:
                bad.append(f"{req.name} is not installed")
                continue
            # A local build tag (2.14.1+cu132) says which wheel, not which release.
            if not req.specifier.contains(have.split("+", 1)[0], prereleases=True):
                bad.append(f"{req.name} {have} does not satisfy '{req.specifier}'")
if bad:
    for item in bad:
        print(f"REQUIREMENTS_FAIL: {item}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

# af_sync_main_venv <venv> <plugin dir> <repo root>
# Brings an existing venv to the tracked requirements, in place. torch goes in
# first, from the channel that fits this machine's GPU, so the requirements
# install keeps that build. If the torch step fails (no network to the PyTorch
# index), the torch floor in requirements.txt still moves a vulnerable torch to
# a current PyPI wheel, and the next run retries the channel build.
af_sync_main_venv() {
    local venv="$1" plugin_dir="$2" repo_root="$3"
    local installer="$repo_root/scripts/install_pytorch.sh"
    local channel=""
    channel="$(cd "$repo_root" && "$repo_root/backend/venv/bin/python" -m backend.services.hardware_policy torch_channel 2>/dev/null || echo "")"

    "$venv/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
    if [ -f "$installer" ]; then
        TARGET_VENV="$venv" GUAARDVARK_TORCH_CHANNEL="$channel" bash "$installer" --venv "$venv" \
            || echo "Warning: install_pytorch.sh returned non-zero (continuing with requirements)"
    else
        echo "Warning: $installer missing; torch comes from the default PyPI wheel"
    fi
    "$venv/bin/python" -m pip install -r "$plugin_dir/requirements.txt" || return 1
    "$venv/bin/python" -m pip install --no-deps -r "$plugin_dir/requirements-nodeps.txt" || return 1
}
