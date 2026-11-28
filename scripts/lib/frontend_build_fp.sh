#!/bin/bash
# scripts/lib/frontend_build_fp.sh — fingerprint of the production bundle's inputs.
# Sourced by start.sh; run directly (with the checkout as the only argument) by
# scripts/frontend_rebuild.py. Both stamp frontend/dist/.build_fp with it, so a
# dist either of them built is recognised by the other.

# Fingerprint of everything the production bundle is built from: the frontend
# tree, extension frontends, and the VITE_* environment. VITE_ALLOWED_HOSTS and
# VITE_PORT are left out: they configure the server at serve time, never reach
# the bundle, and differ per machine and network. Empty outside a git checkout.
frontend_build_fp() {
    git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
    {
        git -C "$SCRIPT_DIR" rev-parse HEAD:frontend
        git -C "$SCRIPT_DIR" diff HEAD --binary -- frontend
        git -C "$SCRIPT_DIR" ls-files -z --others --exclude-standard -- frontend \
            | (cd "$SCRIPT_DIR" && xargs -0 -r sha256sum)
        (
            cd "$SCRIPT_DIR" || exit 0
            find frontend -maxdepth 1 -name '.env*' -type f -print0
            # Extension folders may be symlinks into other checkouts.
            for d in extensions/[!_.]*/frontend; do
                [ -d "$d" ] && find -L "$d" -name node_modules -prune -o -type f -print0
            done
        ) | sort -z | (cd "$SCRIPT_DIR" && xargs -0 -r sha256sum)
        env | grep '^VITE_' | grep -vE '^VITE_(ALLOWED_HOSTS|PORT)=' | sort
    } 2>/dev/null | sha256sum | cut -d' ' -f1
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    SCRIPT_DIR="${1:?usage: frontend_build_fp.sh <checkout>}"
    frontend_build_fp
fi
