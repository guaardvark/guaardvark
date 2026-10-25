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
#   scripts/check_portable.sh --file FILE [TO]  scan one file's whole contents, for a
#                                              document leaving the machine by a route
#                                              git never sees (a mailbox file, a report).
#                                              TO names the recipient: a local pattern
#                                              scoped to them is theirs to read, and is
#                                              skipped for that file only.
#
# Two untracked lists extend it per-clone, because naming their contents in a
# tracked file would publish exactly what they exist to withhold:
#   scripts/.portable-local-patterns   content patterns (box nicknames, ...)
#   scripts/.portable-local-paths      whole files that may never be committed
#
# Install the hooks, these and the inbound guard's, with scripts/install_hooks.sh
# (works from a worktree: every worktree shares the main repo's hooks directory).
#
# Exits non-zero on a finding.
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE="${1:-tracked}"
TARGET="${2:-}"
RECIPIENT="${3:-}"

# Validate the mode before anything scans. An unknown option used to fall through
# to the tracked-tree scan, which answers a different question and usually passes:
# a caller asking "is THIS file safe to send?" would be told yes without the file
# ever being read. An unusable invocation fails loudly instead.
case "$MODE" in
    tracked|--staged) ;;
    --message|--range|--file)
        [ -n "$TARGET" ] || { echo "check_portable.sh: $MODE needs an argument" >&2; exit 2; } ;;
    *)
        echo "check_portable.sh: unknown option '$MODE'" >&2
        echo "Usage: check_portable.sh [--staged | --message FILE | --range REVS | --file FILE]" >&2
        exit 2 ;;
esac
if [ "$MODE" = "--file" ] && [ ! -f "$TARGET" ]; then
    echo "check_portable.sh: no such file: $TARGET" >&2
    exit 2
fi

# Paths allowed to contain the generic patterns below, as extended regex matched
# against the repo-relative path. Tests need fake home directories; the
# architecture doc, funding links and brand config legitimately name the
# project's owner. The local patterns are not exempt anywhere (see run_rules).
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
# Worktrees have no copy of the untracked lists: they live in the main working
# tree, whose git directory every worktree shares.
COMMON_ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
LOCAL_PATTERNS="${PORTABLE_LOCAL_PATTERNS:-$COMMON_ROOT/scripts/.portable-local-patterns}"
LOCAL_RULES=""
if [ -f "$LOCAL_PATTERNS" ]; then
    LOCAL_RULES="$(grep -vE '^\s*(#|$)' "$LOCAL_PATTERNS")"
fi

# Some files must never be committed at all: private working documents, local
# backlogs, operator notes. Naming them in the tracked .gitignore would publish
# the very names being withheld, so they are kept out of `git add` per-clone via
# .git/info/exclude and enforced here from an untracked list, one
# "path-regex<TAB>explanation" per line.
LOCAL_PATHS="${PORTABLE_LOCAL_PATHS:-$COMMON_ROOT/scripts/.portable-local-paths}"

status=0

# Scan the full content of every tracked file. This is the CI gate: it proves
# the committed tree is clean, but it can only report a violation that is
# already public.
scan_tracked() {
    local pattern="$1" explanation="$2" file hits hit
    while IFS= read -r -d '' file; do
        [ "$USE_ALLOW" = 1 ] && [[ "$file" =~ $ALLOW ]] && continue
        if hits=$(grep -nE -- "$pattern" "$file" 2>/dev/null); then
            while IFS= read -r hit; do
                echo "✗ ${file}:${hit%%:*} — ${explanation}"
                status=1
            done <<< "$hits"
        fi
    done < <(git ls-files -z)
}

# The added lines of the commit or the push, read out of git once: their text in
# one file and, line for line, where each came from in another ("<commit><TAB>
# <path>", the commit empty for staged lines, the path empty for a line of a
# commit message). Each rule is then one grep over all of the text; a grep per
# added line costs a process per line, hours on a commit of a few hundred
# thousand lines.
TAB=$'\t'
ADDED=""
collect_added() {
    local commit
    ADDED=$(mktemp -d "${TMPDIR:-/tmp}/check_portable.XXXXXX") || exit 2
    trap 'rm -rf "$ADDED"' EXIT
    : > "$ADDED/text"
    : > "$ADDED/where"
    # Fixed prefixes: a user's diff.noprefix or diff.mnemonicPrefix would change
    # the "+++ b/" headers the parser reads files from.
    local diff_opts=(--unified=0 --no-color --no-ext-diff --src-prefix=a/ --dst-prefix=b/)
    if [ "$MODE" = "--staged" ]; then
        git diff --cached "${diff_opts[@]}" | split_patch ""
        return
    fi
    for commit in $(git rev-list $TARGET 2>/dev/null); do
        git log -1 --format=%B "$commit" | split_message "$commit"
        git show --format= "${diff_opts[@]}" "$commit" | split_patch "$commit"
    done
}

# Added lines of a patch, each beside the file it adds to. Headers are only read
# outside a hunk: an added line whose text starts with "++ b/" looks exactly like
# a "+++ b/" header otherwise. Git ends a header with a tab when the path holds a
# space, and quotes a path holding other unusual characters (+++ "b/...").
split_patch() {
    LC_ALL=C awk -v label="$1" -v text="$ADDED/text" -v where="$ADDED/where" '
        /^diff --/ { hunk = 0; file = ""; next }
        /^@@/      { hunk = 1; next }
        !hunk && /^\+\+\+ / {
            name = substr($0, 5); sub(/\t$/, "", name); file = ""
            if (name ~ /^b\//) file = substr(name, 3)
            else if (name ~ /^"b\//) file = substr(name, 4, length(name) - 4)
            next
        }
        hunk && /^\+/ && file != "" { print substr($0, 2) >> text; print label "\t" file >> where }
    '
}

split_message() {
    LC_ALL=C awk -v label="$1" -v text="$ADDED/text" -v where="$ADDED/where" '
        { print >> text; print label "\t" >> where }
    '
}

# The added lines a pattern matches, as "<commit><TAB><path><TAB><text>".
added_matches() {
    grep -anE -- "$1" "$ADDED/text" | LC_ALL=C awk -v where="$ADDED/where" '{
        n = index($0, ":"); want = substr($0, 1, n - 1) + 0
        while (at < want && (getline from < where) > 0) at++
        print from "\t" substr($0, n + 1)
    }'
}

# Scan only the lines this commit adds. This is the gate that actually prevents
# a leak, so it runs against content that has never left the machine.
scan_staged() {
    local pattern="$1" explanation="$2" hit file content
    while IFS= read -r hit; do
        hit="${hit#*"$TAB"}"
        file="${hit%%"$TAB"*}"
        content="${hit#*"$TAB"}"
        [ "$USE_ALLOW" = 1 ] && [[ "$file" =~ $ALLOW ]] && continue
        echo "✗ ${file} — ${explanation}"
        echo "    ${content:0:120}"
        status=1
    done < <(added_matches "$pattern")
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
            echo "✗ ${file} — ${explanation}"
            status=1
        done < <(printf '%s\n' "$listing" | grep -aE -- "$pattern")
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

# One file's entire contents, for a document that leaves the machine by a route
# git never sees: a file written into a client's mailbox, a report handed over.
# Unlike a commit message there is nothing to skip — a leading '#' here is a
# Markdown heading, which can name a machine as readily as prose can.
scan_file() {
    local pattern="$1" explanation="$2" hit
    while IFS= read -r hit; do
        [ -z "$hit" ] && continue
        echo "✗ ${TARGET}:${hit%%:*} — ${explanation}"
        echo "    $(printf '%s' "${hit#*:}" | cut -c1-120)"
        status=1
    done < <(grep -nE -- "$pattern" "$TARGET" | head -5)
}

# Everything a push would publish: the message and the added lines of each
# commit REVS selects. The last gate before content leaves the machine, and the
# only one that sees commits made with --no-verify or before the hooks existed.
scan_range() {
    local pattern="$1" explanation="$2" hit commit file content subject shown=""
    while IFS= read -r hit; do
        commit="${hit%%"$TAB"*}"
        hit="${hit#*"$TAB"}"
        file="${hit%%"$TAB"*}"
        content="${hit#*"$TAB"}"
        if [ -z "$file" ]; then
            # One line is enough to refuse a message.
            [ "$commit" = "$shown" ] && continue
            shown="$commit"
            echo "✗ ${commit:0:7} message — ${explanation}"
            echo "    ${content}"
        else
            [ "$USE_ALLOW" = 1 ] && [[ "$file" =~ $ALLOW ]] && continue
            subject=$(git log -1 --format=%s "$commit")
            echo "✗ ${commit:0:7} ${file} — ${explanation}  (${subject:0:50})"
            echo "    ${content:0:120}"
        fi
        status=1
    done < <(added_matches "$pattern")
}

[ "$MODE" = "--file" ] || scan_paths
case "$MODE" in --staged|--range) collect_added ;; esac

# The generic rules honour ALLOW; the local ones apply to every file, because a
# box nickname or a client's name has no legitimate place in a test or README.
run_rules() {
    USE_ALLOW="$1"
    while IFS=$'\t' read -r pattern explanation scope; do
        [ -z "$pattern" ] && continue
        # A pattern scoped to a recipient guards their name against the public repo,
        # not against themselves: a report written for them may say who they are.
        # Every other pattern still applies — our machines and identity are not theirs.
        if [ "$MODE" = "--file" ] && [ -n "$scope" ] && [ "$scope" = "$RECIPIENT" ]; then
            continue
        fi
        case "$MODE" in
            --staged)  scan_staged "$pattern" "$explanation" ;;
            --message) scan_message "$pattern" "$explanation" ;;
            --range)   scan_range "$pattern" "$explanation" ;;
            --file)    scan_file "$pattern" "$explanation" ;;
            *)         scan_tracked "$pattern" "$explanation" ;;
        esac
    done <<< "$2"
}
run_rules 1 "$RULES"
run_rules 0 "$LOCAL_RULES"

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
        --file)
            echo "Refusing the file: $TARGET names something that does not leave this"
            echo "machine. Reword it — describe the role, not the box, project or person." ;;
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
    --file)    echo "✓ $TARGET carries no machine-specific paths, identity or secrets." ;;
    *)         echo "✓ No machine-specific paths or hosts in tracked files." ;;
esac
