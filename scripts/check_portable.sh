#!/usr/bin/env bash
#
# Refuse machine-specific content, operator identity and secrets in the repo.
#
# This repo is public and is forked into customer projects, so nothing may be
# tied to one machine, one home directory or one person. Paths come from the
# repo root or the environment; comments are written for future contributors,
# never for whoever happens to be running it today.
#
# A push cannot be taken back: once a commit reaches GitHub it is fetched,
# forked and cached, and rewriting history to remove it breaks every fork. Run
# this before the commit, not after — see --staged.
#
# Usage:
#   scripts/check_portable.sh            scan every tracked file (CI, lint)
#   scripts/check_portable.sh --staged   scan lines about to be committed
#
# Install as a pre-commit hook (works from a worktree, where .git is a file and
# every worktree shares the main repo's one hooks directory):
#   ln -sf ../../scripts/pre-commit "$(git rev-parse --git-common-dir)/hooks/pre-commit"
#
# Exits non-zero on a finding.
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE="${1:-tracked}"

# Paths allowed to contain these patterns, as extended regex matched against
# the repo-relative path. Tests need fake home directories; the architecture
# doc, funding links and brand config legitimately name the project's owner.
ALLOW='^(backend/tests/|cli/tests/|frontend/src/.*\.test\.(js|jsx)$|docs/ARCHITECTURE\.md$|\.github/FUNDING\.yml$|README\.md$|LICENSE$|frontend/src/config/brand\.jsx$|scripts/check_portable\.sh$)'

# pattern<TAB>human explanation
#
# Only literal absolute home paths are flagged. A "~/..." string passed through
# expanduser() resolves per-user and is the correct portable pattern, so it is
# deliberately not matched here.
RULES=$(cat <<'PATTERNS'
/home/[a-z]	an absolute Linux home directory — derive from the repo root or read an env var
/Users/[a-z]	an absolute macOS home directory — derive from the repo root or read an env var
[A-Za-z]:\\Users\\	an absolute Windows home directory — derive from the repo root or read an env var
\.ts\.net	a private tailnet hostname
100\.(6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])\.[0-9]+\.[0-9]+	a tailnet IP address
sk-[A-Za-z0-9]{20,}	something shaped like an API secret key
ghp_[A-Za-z0-9]{30,}	something shaped like a GitHub personal access token
github_pat_[A-Za-z0-9_]{50,}	something shaped like a fine-grained GitHub token
AKIA[0-9A-Z]{16}	something shaped like an AWS access key id
xox[baprs]-[A-Za-z0-9-]{10,}	something shaped like a Slack token
AIza[0-9A-Za-z_-]{30,}	something shaped like a Google API key
-----BEGIN [A-Z ]*PRIVATE KEY-----	an embedded private key
PATTERNS
)

# Box nicknames and other identifiers specific to whoever runs this clone do not
# belong in a public file — listing them here would publish the very names the
# rule exists to keep private. Keep them in an untracked local file instead, one
# "pattern<TAB>explanation" per line, and this picks them up automatically.
LOCAL_PATTERNS="${PORTABLE_LOCAL_PATTERNS:-scripts/.portable-local-patterns}"
if [ -f "$LOCAL_PATTERNS" ]; then
    RULES="$RULES"$'\n'"$(grep -vE '^\s*(#|$)' "$LOCAL_PATTERNS")"
fi

status=0

# Scan the full content of every tracked file. This is the CI gate: it proves
# the committed tree is clean, but it can only report a violation that is
# already public.
scan_tracked() {
    local pattern="$1" explanation="$2" file hits hit
    while IFS= read -r -d '' file; do
        [[ "$file" =~ $ALLOW ]] && continue
        if hits=$(grep -nE -- "$pattern" "$file" 2>/dev/null); then
            while IFS= read -r hit; do
                echo "✗ ${file}:${hit%%:*} — ${explanation}"
                status=1
            done <<< "$hits"
        fi
    done < <(git ls-files -z)
}

# Scan only the lines this commit adds. This is the gate that actually prevents
# a leak, so it runs against content that has never left the machine.
scan_staged() {
    local pattern="$1" explanation="$2" file="" line content
    while IFS= read -r line; do
        case "$line" in
            '+++ /dev/null') file="" ;;
            '+++ b/'*)       file="${line#+++ b/}" ;;
            '+'*)
                [ -z "$file" ] && continue
                [[ "$file" =~ $ALLOW ]] && continue
                content="${line#+}"
                if printf '%s' "$content" | grep -qE -- "$pattern"; then
                    echo "✗ ${file} — ${explanation}"
                    echo "    ${content:0:120}"
                    status=1
                fi
                ;;
        esac
    done < <(git diff --cached --unified=0 --no-color)
}

while IFS=$'\t' read -r pattern explanation; do
    [ -z "$pattern" ] && continue
    if [ "$MODE" = "--staged" ]; then
        scan_staged "$pattern" "$explanation"
    else
        scan_tracked "$pattern" "$explanation"
    fi
done <<< "$RULES"

if [ "$status" -ne 0 ]; then
    echo
    if [ "$MODE" = "--staged" ]; then
        echo "Refusing the commit: the staged changes carry machine-specific"
        echo "content, operator identity or a secret. Nothing has been published"
        echo "yet — fix the lines above and stage again."
    else
        echo "Machine-specific content found in tracked files."
    fi
    echo "Derive paths from the repo root (Path(__file__).resolve().parents[N])"
    echo "or read them from an environment variable with a portable default."
    exit 1
fi

if [ "$MODE" = "--staged" ]; then
    echo "✓ Staged changes carry no machine-specific paths, identity or secrets."
else
    echo "✓ No machine-specific paths or hosts in tracked files."
fi
