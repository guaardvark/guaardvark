#!/bin/bash
# scripts/lib/venv_pins.sh — offline probe for the ML-stack pins that torch
# installs keep knocking loose. Sourced by start.sh and heal_backend_venv.sh.
#
# Both scripts used to run `pip install --no-deps --force-reinstall numpy… setuptools…`
# unconditionally after every torch step. --force-reinstall consults the index even
# when nothing would change, so on a flaky link a no-op re-pin failed with pip's
# misleading "ResolutionImpossible: numpy<2.0,>=1.26.4 vs (constraint) numpy<2.0,>=1.26.4"
# (client box, 2026-08-31). Probe first; re-pin only what is actually wrong.

# The pins themselves. requirements.txt carries the same numpy line; setuptools is
# re-asserted here because the PyTorch index only serves 78.1.0 and llama-index
# needs >=80.9.0 (see backend/constraints.txt for why it is not a constraint).
GV_ML_PINS=('numpy<2.0,>=1.26.4' 'setuptools>=80.9.0,<81')

# venv_pins_violated <venv-python> [spec...]
#   Prints the specs whose installed version does not satisfy them, one per line,
#   and returns 0. Prints nothing and returns 1 when every spec already holds.
#   A missing package counts as a violation. No network.
venv_pins_violated() {
    local py="$1"; shift
    if [ ! -x "$py" ]; then
        printf '%s\n' "$@"
        return 0
    fi
    "$py" - "$@" <<'EOF'
import sys
from importlib.metadata import PackageNotFoundError, version

try:
    from packaging.requirements import Requirement
except ImportError:
    try:
        from pip._vendor.packaging.requirements import Requirement
    except ImportError:
        print("\n".join(sys.argv[1:]))
        sys.exit(0)

bad = []
for spec in sys.argv[1:]:
    req = Requirement(spec)
    try:
        installed = version(req.name)
    except PackageNotFoundError:
        bad.append(spec)
        continue
    if not req.specifier.contains(installed, prereleases=True):
        bad.append(spec)

print("\n".join(bad))
sys.exit(0 if bad else 1)
EOF
}
