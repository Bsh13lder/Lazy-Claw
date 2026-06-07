#!/bin/sh
# docker-entrypoint.sh — make Claude CLI auth persist inside Docker.
#
# Why this exists:
#   - macOS hosts store the OAuth credential in Keychain, which Docker
#     can't read. So the host ~/.claude/ alone can't authenticate the
#     in-container `claude` CLI.
#   - `claude /login` writes via atomic rename (write tmp + rename(2)).
#     A symlink at ~/.claude/.credentials.json is REPLACED by a real
#     file by rename(2), defeating any symlink-to-volume trick.
#
# Solution:
#   - ~/.claude is a writable Docker named volume (compose: claude_data).
#     `claude /login` writes its credential as a real file inside the
#     volume; atomic rename works because both tmp and target live in
#     the same mount.
#   - Read-only host mount at /home/lazyclaw/.claude-host gives access
#     to host plugins/settings/history. Entrypoint symlinks each entry
#     into the volume on first boot (when the volume is empty).
#   - Each entry is symlinked only if the volume doesn't already have
#     something at that name — so claude can replace a file, install a
#     plugin, or write the credential without our links getting in the
#     way.

set -e

RO_HOST="/home/lazyclaw/.claude-host"
CLAUDE_HOME="/home/lazyclaw/.claude"

mkdir -p "$CLAUDE_HOME"

if [ -d "$RO_HOST" ]; then
    for item in "$RO_HOST"/* "$RO_HOST"/.[!.]*; do
        [ -e "$item" ] || continue
        name="$(basename "$item")"
        target="$CLAUDE_HOME/$name"
        # Never seed:
        #   - the credential file (claude owns it in the volume, atomic-renames on login)
        #   - SESSION/PLAN/MEMORY state — this is the host user's PERSONAL Claude Code
        #     data. Symlinking projects/ + memory/ exposed the user's personal
        #     transcripts + auto-memory to the in-container claude (inbound leak)
        #     and routed lazyclaw's own sessions back onto the host disk (outbound).
        #     plans/ + sessions/ + session-data/ + session-env/ are the same class
        #     of personal state (plans/ is the dir the retired `_ingest_claude_plans`
        #     bridge used to mirror — keep it out of reach on principle). The agent's
        #     sessions live in the writable volume, fully isolated.
        #     (Belt-and-braces: the provider also sets setting_sources=[] + an
        #     isolated cwd, so even an unexpected symlink wouldn't load/co-mingle.)
        case "$name" in
            .credentials.json) continue ;;
            projects|memory|plans|sessions|session-data|session-env|todos|shell-snapshots|history.jsonl|history)
                # On an EXISTING volume from a prior boot (before this
                # exclusion existed), $target may already be a STALE symlink
                # into the read-only host mount. The seed guard below would
                # never remove it. Remove it here so the host's personal
                # session/plan/memory state is unreachable and our own writes land
                # in the writable volume. Only remove symlinks pointing into
                # $RO_HOST — never a real dir the volume legitimately owns.
                if [ -L "$target" ]; then
                    link_dest="$(readlink "$target" 2>/dev/null || echo '')"
                    case "$link_dest" in
                        "$RO_HOST"/*)
                            rm -f "$target"
                            echo "entrypoint: removed stale host symlink $target -> $link_dest" >&2
                            ;;
                    esac
                fi
                continue ;;
        esac
        # Only seed if nothing exists at this path. Honors prior writes,
        # claude-managed files, and dangling symlinks (-L catches those).
        if [ ! -e "$target" ] && [ ! -L "$target" ]; then
            ln -s "$item" "$target"
        fi
    done
fi

exec "$@"
