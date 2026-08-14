#!/usr/bin/env bash
#
# write-build-info.sh — bake a deploy stamp so "what code is prod running?"
# is always answerable.
#
# LazyClaw images are built from the working tree, not from a git ref, so a
# `docker compose build` silently captures whatever is on disk. That produced
# the deploy-drift incident class: fixes committed-but-not-deployed and
# deployed-but-not-committed (twice each in one month), with no way to tell
# after the fact.
#
# This script records the git identity of the tree at build time into
# BUILD_INFO.json at the repo root. `make rebuild` runs it BEFORE
# `docker compose build`; the Dockerfile COPYs the file into the image (and
# stubs it when absent), and the gateway exposes it at GET /api/health as the
# `build` object. So: script -> file -> image -> endpoint.
#
# Usage:
#   scripts/write-build-info.sh [OUTPUT_PATH]
#
# Env:
#   LAZYCLAW_BUILD_INFO_REPO   repo to inspect (default: this script's parent)
#
# Contract:
#   - ALWAYS writes valid JSON (a {"sha":"unknown",...} stub when git is
#     unavailable / the directory is not a repo).
#   - ALWAYS exits 0. A stamping problem must never block a deploy.
#   - Warns LOUDLY on a dirty tree but does NOT block: this repo deliberately
#     deploys work-in-progress sometimes.
#   - JSON goes to the output FILE; the human banner goes to stderr, so stdout
#     stays free for piping.

# Deliberately no `-e`: a failed git probe degrades to "unknown", never aborts.
set -uo pipefail

REPO_ROOT="${LAZYCLAW_BUILD_INFO_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT_PATH="${1:-$REPO_ROOT/BUILD_INFO.json}"
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GENERATOR="scripts/write-build-info.sh"

# Keep the banner readable when a tree has dozens of dirty files. The JSON
# always carries the complete list.
MAX_LISTED_FILES=40

say() { printf '%s\n' "$*" >&2; }

# Escape a scalar for embedding in a JSON string: backslash, double quote,
# then strip control characters (which are illegal raw inside JSON strings).
json_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr -d '\000-\037'
}

write_json() {
    # $1 sha, $2 short_sha, $3 branch, $4 describe, $5 dirty(true|false|null),
    # $6 dirty_files JSON array
    local target_dir
    target_dir="$(dirname "$OUT_PATH")"
    mkdir -p "$target_dir" 2>/dev/null

    if ! cat > "$OUT_PATH" <<EOF
{
  "sha": "$1",
  "short_sha": "$2",
  "branch": "$3",
  "describe": "$4",
  "dirty": $5,
  "dirty_files": $6,
  "built_at": "$BUILT_AT",
  "generator": "$GENERATOR"
}
EOF
    then
        say "build-info: WARNING could not write $OUT_PATH — the image will"
        say "build-info:         report \"sha\": \"unknown\" at /api/health."
        return 1
    fi
    return 0
}

write_stub() {
    # $1 = human reason
    write_json "unknown" "unknown" "unknown" "unknown" "null" "[]"
    say ""
    say "build-info: no git identity available ($1)."
    say "build-info: wrote an \"unknown\" stamp to $OUT_PATH — /api/health will"
    say "build-info: report sha=unknown for this image."
    say ""
    exit 0
}

if ! command -v git >/dev/null 2>&1; then
    write_stub "git is not installed"
fi

if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    write_stub "$REPO_ROOT is not a git repository"
fi

SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)"
[ -n "$SHA" ] || SHA="unknown"
SHORT_SHA="$(git -C "$REPO_ROOT" rev-parse --short=12 HEAD 2>/dev/null)"
[ -n "$SHORT_SHA" ] || SHORT_SHA="unknown"
BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)"
[ -n "$BRANCH" ] || BRANCH="unknown"
DESCRIBE="$(git -C "$REPO_ROOT" describe --tags --always --dirty 2>/dev/null)"
[ -n "$DESCRIBE" ] || DESCRIBE="unknown"

# Tracked changes only. Untracked files (BUILD_INFO.json itself, scratch
# notes, build artefacts) are not part of "what code is in this image" — and
# BUILD_INFO.json in particular must never mark the tree dirty on its own.
#
# `-z` (NUL-separated) rather than plain porcelain: git QUOTES any path
# containing a space or non-ASCII byte, so the naive parse recorded
# `"a file with spaces.py"` with the quote marks baked into the value. NUL
# records are unambiguous. Note the pipeline must be a process substitution,
# not a pipe: a pipe would run the loop in a subshell and lose the counters
# (and a command substitution can't hold NUL bytes at all).
DIRTY="false"
DIRTY_FILES_JSON="[]"
DIRTY_COUNT=0
DIRTY_LIST=""
items=""

while IFS= read -r -d '' entry; do
    [ -n "$entry" ] || continue
    # porcelain v1 record: "XY PATH"
    status="${entry:0:2}"
    path="${entry:3}"
    case "$status" in
        R*|C*)
            # Rename/copy emits the ORIGINAL path as the next NUL record.
            if IFS= read -r -d '' orig && [ -n "$orig" ]; then
                path="$orig -> $path"
            fi
            ;;
    esac
    items="${items:+$items, }\"$(json_escape "$path")\""
    DIRTY_COUNT=$((DIRTY_COUNT + 1))
    DIRTY_LIST="${DIRTY_LIST}${path}"$'\n'
done < <(git -C "$REPO_ROOT" status --porcelain -z --untracked-files=no 2>/dev/null)

if [ "$DIRTY_COUNT" -gt 0 ]; then
    DIRTY="true"
    DIRTY_FILES_JSON="[$items]"
fi

write_json \
    "$(json_escape "$SHA")" \
    "$(json_escape "$SHORT_SHA")" \
    "$(json_escape "$BRANCH")" \
    "$(json_escape "$DESCRIBE")" \
    "$DIRTY" \
    "$DIRTY_FILES_JSON"

STAMP="$SHORT_SHA"
if [ "$DIRTY" = "true" ]; then
    STAMP="$SHORT_SHA-dirty"

    say ""
    say "################################################################"
    say "#                                                              #"
    say "#   WARNING — DEPLOYING AN UNCOMMITTED WORKING TREE            #"
    say "#                                                              #"
    say "################################################################"
    say ""
    say "  $DIRTY_COUNT tracked file(s) differ from HEAD ($SHORT_SHA):"
    say ""
    printf '%s' "$DIRTY_LIST" | head -n "$MAX_LISTED_FILES" | sed 's/^/      - /' >&2
    if [ "$DIRTY_COUNT" -gt "$MAX_LISTED_FILES" ]; then
        say "      ... and $((DIRTY_COUNT - MAX_LISTED_FILES)) more"
    fi
    say ""
    say "  This image will NOT be reproducible from git. If it goes wrong,"
    say "  \`git checkout $SHORT_SHA\` does NOT get you back to what shipped."
    say ""
    say "  Proceeding anyway (deploying WIP is allowed here)."
    say "  /api/health will report \"dirty\": true so it stays traceable."
    say ""
    say "################################################################"
    say ""
else
    say ""
    say "  build-info: clean tree ✓"
fi

say "  Deploying $STAMP  (branch $BRANCH, built_at $BUILT_AT)"
say "  Stamp written to: $OUT_PATH"
say ""

exit 0
