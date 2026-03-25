#!/bin/bash
# LazyClaw + Claude Memory Transfer Script
# Run on NEW Mac after copying files over

set -e

OLD_USER="solalicialien"
NEW_USER="$(whoami)"

CLAUDE_DIR="$HOME/.claude"
PROJECTS_DIR="$CLAUDE_DIR/projects"

echo "=== Claude Memory Transfer ==="
echo "Old user: $OLD_USER"
echo "New user: $NEW_USER"
echo ""

# Step 1: Check if .claude exists
if [ ! -d "$CLAUDE_DIR" ]; then
    echo "ERROR: ~/.claude/ not found. Copy it from old Mac first."
    exit 1
fi

# Step 2: Rename project memory folders if username changed
if [ "$OLD_USER" != "$NEW_USER" ]; then
    echo "Username changed! Renaming project memory folders..."
    echo ""

    cd "$PROJECTS_DIR"
    for old_dir in *"$OLD_USER"*; do
        if [ -d "$old_dir" ]; then
            new_dir="${old_dir//$OLD_USER/$NEW_USER}"
            echo "  $old_dir"
            echo "  -> $new_dir"
            echo ""
            mv "$old_dir" "$new_dir"
        fi
    done

    echo "Done renaming folders."
else
    echo "Username unchanged — no renames needed."
fi

echo ""

# Step 3: Verify key files
echo "=== Verification ==="

check_file() {
    if [ -e "$1" ]; then
        echo "  OK: $1"
    else
        echo "  MISSING: $1"
    fi
}

check_file "$CLAUDE_DIR/settings.json"
check_file "$CLAUDE_DIR/settings.local.json"
check_file "$CLAUDE_DIR/rules/"
check_file "$PROJECTS_DIR/"

# Find lazyclaw project memory
LC_PROJECT=$(find "$PROJECTS_DIR" -maxdepth 1 -name "*lazyclaw*" -type d 2>/dev/null | head -1)
if [ -n "$LC_PROJECT" ]; then
    echo "  OK: LazyClaw memory at $LC_PROJECT"
    check_file "$LC_PROJECT/memory/MEMORY.md"
else
    echo "  MISSING: No LazyClaw project memory found"
fi

echo ""
echo "=== What to do next ==="
echo "1. cd into lazyclaw folder"
echo "2. Check .env file is present"
echo "3. pip install -e ."
echo "4. Run: claude  (should see all your memory)"
