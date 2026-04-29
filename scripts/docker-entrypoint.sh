#!/bin/sh
# docker-entrypoint.sh — make Claude CLI auth work inside the container.
#
# macOS hosts store Claude Code's OAuth credential in the Keychain
# (`Claude Code-credentials`), which is invisible to Docker. The host
# ~/.claude/ directory has settings/plugins/history but NOT the token.
# Mounting it read-only is therefore not enough on a Mac host.
#
# Strategy:
#   1. Read-only host mount lives at /home/lazyclaw/.claude-host (compose).
#   2. Persistent Docker volume mounts at /home/lazyclaw/.claude-creds
#      and holds .credentials.json (and only that).
#   3. This script builds a WRITABLE /home/lazyclaw/.claude that
#      symlinks each entry from the host mount, except .credentials.json
#      which is symlinked into the persistent volume.
#
# Net effect: user runs `claude /login` once inside the container,
# the credential lands in the named volume, and it survives
# `docker compose down` and image rebuilds. Host plugins/settings
# stay shared read-only.

set -e

RO_HOST="/home/lazyclaw/.claude-host"
CRED_VOL="/home/lazyclaw/.claude-creds"
CLAUDE_HOME="/home/lazyclaw/.claude"

mkdir -p "$CLAUDE_HOME" "$CRED_VOL"

if [ -d "$RO_HOST" ]; then
    for item in "$RO_HOST"/* "$RO_HOST"/.[!.]*; do
        [ -e "$item" ] || continue
        name="$(basename "$item")"
        case "$name" in
            .credentials.json) continue ;;
        esac
        target="$CLAUDE_HOME/$name"
        if [ -L "$target" ] || [ ! -e "$target" ]; then
            ln -snf "$item" "$target"
        fi
    done
fi

ln -snf "$CRED_VOL/.credentials.json" "$CLAUDE_HOME/.credentials.json"

exec "$@"
