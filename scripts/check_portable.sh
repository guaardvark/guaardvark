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
#   scripts/check_portable.sh                  scan every tracked file (CI, lint)
#   scripts/check_portable.sh --staged         scan lines about to be committed
#   scripts/check_portable.sh --message FILE   scan a commit message (commit-msg hook)
#   scripts/check_portable.sh --range REVS     scan messages and added lines of the
#                                              commits REVS selects (pre-push hook),
#                                              e.g. origin/main..HEAD
#
# Two untracked lists extend it per-clone, because naming their contents in a
# tracked file would publish exactly what they exist to withhold:
#   scripts/.portable-local-patterns   content patterns (box nicknames, ...)
#   scripts/.portable-local-paths      whole files that may never be committed
#
# Install the hooks (works from a worktree, where .git is a file and every
# worktree shares the main repo's one hooks directory):
#   for h in pre-commit commit-msg pre-push; do
#     ln -sf ../../scripts/$h "$(git rev-parse --git-common-dir)/hooks/$h"
#   done
#
# Exits non-zero on a finding.
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE="${1:-tracked}"
TARGET="${2:-}"

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

# Some files must never be committed at all: private working documents, local
# backlogs, operator notes. Naming them in the tracked .gitignore would publish
# the very names being withheld, so they are kept out of `git add` per-clone via
# .git/info/exclude and enforced here from an untracked list, one
# "path-regex<TAB>explanation" per line.
LOCAL_PATHS="${PORTABLE_LOCAL_PATHS:-scripts/.portable-local-paths}"

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

# Refuse whole files by path. .git/info/exclude already keeps these out of a
# `git add -A`, but that is per-clone and does not stop an explicit `git add
# <path>`, so this is the gate that does not depend on a clone being set up
# correctly. Missing list means nothing to enforce, which is the correct
# default for a fresh clone that has no private files in it.
scan_paths() {
    local listing pattern explanation file
    [ -f "$LOCAL_PATHS" ] || return 0
    if [ "$MODE" = "--staged" ]; then
        listing=$(git diff --cached --name-only --diff-filter=d)
    else
        listing=$(git ls-files)
    fi
    [ -z "$listing" ] && return 0
    while IFS=$'\t' read -r pattern explanation; do
        [ -z "$pattern" ] && continue
        while IFS= read -r file; do
            [ -z "$file" ] && continue
            if printf '%s' "$file" | grep -qE -- "$pattern"; then
                echo "✗ ${file} — ${explanation}"
                status=1
            fi
        done <<< "$listing"
    done < <(grep -vE '^\s*(#|$)' "$LOCAL_PATHS")
}

# A commit message travels with the push as surely as the diff does, and the
# pre-commit gate never sees it.
scan_message() {
    local pattern="$1" explanation="$2" hit
    [ -f "$TARGET" ] || return 0
    if hit=$(grep -vE '^\s*#' "$TARGET" | grep -nE -- "$pattern" | head -1); then
        echo "✗ commit message line ${hit%%:*} — ${explanation}"
        echo "    ${hit#*:}"
        status=1
    fi
}

# Everything a push would publish: the message and the added lines of each
# commit REVS selects. The last gate before content leaves the machine, and the
# only one that sees commits made with --no-verify or before the hooks existed.
scan_range() {
    local pattern="$1" explanation="$2" commit subject hit file="" line content
    for commit in $(git rev-list $TARGET 2>/dev/null); do
        subject=$(git log -1 --format=%s "$commit")
        if hit=$(git log -1 --format=%B "$commit" | grep -nE -- "$pattern" | head -1); then
            echo "✗ ${commit:0:7} message — ${explanation}"
            echo "    ${hit#*:}"
            status=1
        fi
        file=""
        while IFS= read -r line; do
            case "$line" in
                '+++ /dev/null') file="" ;;
                '+++ b/'*)       file="${line#+++ b/}" ;;
                '+'*)
                    [ -z "$file" ] && continue
                    [[ "$file" =~ $ALLOW ]] && continue
                    content="${line#+}"
                    if printf '%s' "$content" | grep -qE -- "$pattern"; then
                        echo "✗ ${commit:0:7} ${file} — ${explanation}  (${subject:0:50})"
                        echo "    ${content:0:120}"
                        status=1
                    fi
                    ;;
            esac
        done < <(git show --format= --unified=0 --no-color "$commit")
    done
}

scan_paths

while IFS=$'\t' read -r pattern explanation; do
    [ -z "$pattern" ] && continue
    case "$MODE" in
        --staged)  scan_staged "$pattern" "$explanation" ;;
        --message) scan_message "$pattern" "$explanation" ;;
        --range)   scan_range "$pattern" "$explanation" ;;
        *)         scan_tracked "$pattern" "$explanation" ;;
    esac
done <<< "$RULES"

if [ "$status" -ne 0 ]; then
    echo
    case "$MODE" in
        --staged)
            echo "Refusing the commit: the staged changes carry machine-specific"
            echo "content, operator identity, a secret, or a file that is not the"
            echo "public repo's to publish. Nothing has been published yet — fix"
            echo "the findings above and stage again." ;;
        --message)
            echo "Refusing the commit: the message names something this repo does"
            echo "not publish. Reword it — describe the role, not the box or project." ;;
        --range)
            echo "Refusing the push: a commit in $TARGET would publish content this"
            echo "repo keeps private. Amend or rewrite it before it leaves the machine." ;;
        *)
            echo "Machine-specific content found in tracked files." ;;
    esac
    echo "For content: derive paths from the repo root"
    echo "(Path(__file__).resolve().parents[N]) or read them from an environment"
    echo "variable with a portable default. For a whole file: unstage it — it is"
    echo "listed as local-only and belongs outside the commit entirely."
    exit 1
fi

case "$MODE" in
    --staged)  echo "✓ Staged changes carry no machine-specific paths, identity or secrets." ;;
    --message) echo "✓ Commit message carries no private names." ;;
    --range)   echo "✓ $TARGET carries no machine-specific paths, identity or secrets." ;;
    *)         echo "✓ No machine-specific paths or hosts in tracked files." ;;
esac
