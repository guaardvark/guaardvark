#!/usr/bin/env bash
#
# Install this clone's git hooks: the portability guard (what leaves) and the
# inbound guard (what arrives).
#
#   pre-commit, commit-msg, pre-push      symlinks to the tracked scripts, so a
#                                         change to them applies at once
#   pre-merge-commit, reference-transaction
#                                         copies, plus a pinned copy of the
#                                         inbound engine in
#                                         <git-common-dir>/inbound-guard/engine
#
# The inbound hooks run from a copy on purpose: incoming code is judged by the
# engine installed here, never by the version it carries. After a change to the
# inbound guard has been read and accepted, run this again to adopt it.
#
# The inbound guard does nothing until a mode is set:
#   git config inboundguard.mode observe   record verdicts and print findings
#   git config inboundguard.mode enforce   also refuse a held merge until approved
#
# Every worktree shares one hooks directory, so this installs for all of them.
#
# Usage:
#   scripts/install_hooks.sh           install or update
#   scripts/install_hooks.sh --check   report whether what is installed matches
#                                      the tracked hooks and engine (exit 1 if not)
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
common="$(git rev-parse --path-format=absolute --git-common-dir)"
hooks="$(git rev-parse --path-format=absolute --git-path hooks)"
pinned="$common/inbound-guard/engine"
outbound=(pre-commit commit-msg pre-push)
inbound=(pre-merge-commit reference-transaction)

tracked_fp="$(python3 scripts/check_inbound.py fingerprint scripts)"

if [ "${1:-}" = "--check" ]; then
    ok=0
    for h in "${outbound[@]}"; do
        if [ "$(readlink "$hooks/$h" 2>/dev/null)" != "../../scripts/$h" ]; then
            echo "✗ $h is not linked to scripts/$h"; ok=1
        fi
    done
    for h in "${inbound[@]}"; do
        if ! cmp -s "scripts/hooks/$h" "$hooks/$h"; then
            echo "✗ $h is missing or differs from scripts/hooks/$h"; ok=1
        fi
    done
    if [ "$(cat "$pinned/PINNED" 2>/dev/null)" != "$tracked_fp" ]; then
        echo "✗ the installed inbound engine differs from scripts/ (or is not installed)"; ok=1
    fi
    [ "$ok" -eq 0 ] && echo "✓ hooks and inbound engine match the tracked copies"
    echo "  inbound guard mode: $(python3 scripts/check_inbound.py mode)"
    exit "$ok"
fi

mkdir -p "$hooks"

# The relative link target assumes the default hooks directory inside .git.
if [ "$hooks" = "$common/hooks" ]; then
    for h in "${outbound[@]}"; do
        ln -sfn "../../scripts/$h" "$hooks/$h"
    done
else
    echo "! core.hooksPath points at $hooks; link the portability hooks there yourself:"
    for h in "${outbound[@]}"; do echo "    ln -sf $PWD/scripts/$h $hooks/$h"; done
fi

for h in "${inbound[@]}"; do
    target="$hooks/$h"
    # Every version of these hooks runs the pinned engine; any other hook there is kept.
    if [ -e "$target" ] && ! grep -q "inbound-guard/engine" "$target" 2>/dev/null; then
        mv "$target" "$target.before-inbound-guard"
        echo "! kept the previous $h as $h.before-inbound-guard"
    fi
    cp "scripts/hooks/$h" "$target"
    chmod +x "$target"
done

rm -rf "$pinned"
mkdir -p "$pinned/inbound_guard"
cp scripts/check_inbound.py "$pinned/"
cp scripts/inbound_guard/*.py scripts/inbound_guard/*.json "$pinned/inbound_guard/"
echo "$tracked_fp" > "$pinned/PINNED"

echo "✓ portability hooks: ${outbound[*]}"
echo "✓ inbound hooks: ${inbound[*]} (engine pinned at $pinned)"
mode="$(python3 scripts/check_inbound.py mode)"
echo "  inbound guard mode: $mode"
if [ "$mode" = "off" ]; then
    echo "  turn it on with: git config inboundguard.mode observe   (or enforce)"
fi
