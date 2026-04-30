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
        # Never seed the credential file from the host — claude owns
        # this inside the volume and atomic-renames it on login.
        case "$name" in
            .credentials.json) continue ;;
        esac
        target="$CLAUDE_HOME/$name"
        # Only seed if nothing exists at this path. Honors prior writes,
        # claude-managed files, and dangling symlinks (-L catches those).
        if [ ! -e "$target" ] && [ ! -L "$target" ]; then
            ln -s "$item" "$target"
        fi
    done
fi

exec "$@"
