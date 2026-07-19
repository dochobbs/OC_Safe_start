#!/usr/bin/env bash
# Remove safe-start cleanly. Your other settings and hooks are untouched.
set -euo pipefail

CLAUDE_DIR="$HOME/.claude"
DEST="$CLAUDE_DIR/skills/safe-start"

if [ -f "$DEST/install/merge_settings.py" ]; then
  python3 "$DEST/install/merge_settings.py" remove "$DEST/hooks" >/dev/null 2>&1 || true
fi

rm -rf "$DEST"
rm -rf "$CLAUDE_DIR/safe-start"

printf '%s\n' "safe-start removed. Your other settings and hooks are untouched."
