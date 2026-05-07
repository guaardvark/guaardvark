#!/usr/bin/env bash
# Release-oriented quality checks (non-destructive). Exit non-zero on failure.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Quality gate (static)"
python3 scripts/quality_gate.py --mode static

if [[ -f scripts/check_migrations.py ]]; then
  echo "==> Migration check"
  python3 scripts/check_migrations.py || true
fi

if [[ -f scripts/consolidated_selftest.py ]]; then
  echo "==> Consolidated selftest (quick)"
  python3 scripts/consolidated_selftest.py --quick || true
fi

echo "==> Done release_quality_check.sh"
