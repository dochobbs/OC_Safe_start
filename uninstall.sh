#!/usr/bin/env bash
# Remove safe-start only after its owned hooks are verifiably deregistered.
set -euo pipefail

CLAUDE_DIR="${HOME:?HOME must be set}/.claude"
DEST="$CLAUDE_DIR/skills/safe-start"
STATE_DIR="$CLAUDE_DIR/safe-start"
MERGER="$DEST/install/merge_settings.py"

say() { printf '%s\n' "$*"; }

if ! command -v python3 >/dev/null 2>&1; then
  say "safe-start: Python 3 is required to deregister the guards safely."
  say "Nothing was removed."
  exit 1
fi

if [ ! -f "$MERGER" ]; then
  say "safe-start: cannot find $MERGER, so guard removal cannot be verified."
  say "Nothing was removed; repair/reinstall safe-start, then uninstall again."
  exit 1
fi

if ! python3 "$MERGER" remove "$DEST/hooks"; then
  say "safe-start: guard deregistration failed."
  say "Nothing was removed; the installed scripts are preserved for recovery."
  exit 1
fi

if ! python3 "$MERGER" verify-absent "$DEST/hooks" >/dev/null; then
  say "safe-start: owned hooks still appear in settings after deregistration."
  say "Nothing was removed; the installed scripts are preserved for recovery."
  exit 1
fi

rm -rf -- "$DEST"
rm -rf -- "$STATE_DIR"

say "safe-start removed. Your other settings and hooks are untouched."
