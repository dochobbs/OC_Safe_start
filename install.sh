#!/usr/bin/env bash
# safe-start installer — one line to a permanent safety net.
#
#   curl -fsSL <url>/install.sh | bash
#
# Installs the safe-start skill + guardrail hooks into ~/.claude, preserving any
# settings and hooks you already have. Remove anytime with the uninstaller.
set -euo pipefail

CLAUDE_DIR="$HOME/.claude"
DEST="$CLAUDE_DIR/skills/safe-start"

say() { printf '%s\n' "$*"; }

say "Installing safe-start…"

# --- prerequisites -------------------------------------------------------- #
if ! command -v python3 >/dev/null 2>&1; then
  say "  ✗ Python 3 is required (it ships with the macOS developer tools:"
  say "    run 'xcode-select --install', then try again)."
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  say "  ✗ git is required. Install it and re-run."
  exit 1
fi

# --- locate the source ---------------------------------------------------- #
# Priority: SAFE_START_SRC env var -> adjacent checkout -> git clone.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
CLEANUP_CLONE=""
if [ -n "${SAFE_START_SRC:-}" ]; then
  SRC="$SAFE_START_SRC"
elif [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/SKILL.md" ] \
  && [ -f "$SCRIPT_DIR/hooks/pretooluse.py" ]; then
  # Require a hook file too, so a curl|bash run from inside some OTHER skill's
  # folder (any SKILL.md) can't be mistaken for a safe-start checkout.
  SRC="$SCRIPT_DIR"
else
  SRC="$(mktemp -d)"
  CLEANUP_CLONE="$SRC"
  REPO_URL="${SAFE_START_REPO:-https://github.com/dochobbs/OC_Safe_start.git}"
  say "  Fetching safe-start…"
  if ! git clone --depth 1 "$REPO_URL" "$SRC" >/dev/null 2>&1; then
    say "  ✗ Could not fetch safe-start from $REPO_URL"
    say "    Set SAFE_START_SRC to a local copy and re-run."
    exit 1
  fi
fi

# --- copy the skill ------------------------------------------------------- #
mkdir -p "$DEST"
# Copy contents; exclude any VCS metadata from a clone.
( cd "$SRC" && tar --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' -cf - . ) | ( cd "$DEST" && tar -xf - )
[ -n "$CLEANUP_CLONE" ] && rm -rf "$CLEANUP_CLONE"

# --- self-test the detection engine + hooks on this machine --------------- #
if python3 "$DEST/tests/test_detectors.py" >/dev/null 2>&1 \
  && python3 "$DEST/tests/test_hooks.py" >/dev/null 2>&1; then
  say "  ✓ detection + hook self-test passed"
else
  say "  ! self-test failed — safety checks may be degraded"
fi

# --- register the guards (merges into your existing settings) ------------- #
if python3 "$DEST/install/merge_settings.py" add "$DEST/hooks" >/dev/null; then
  say "  ✓ guards registered in $CLAUDE_DIR/settings.json (your other hooks kept)"
else
  say "  ! couldn't register the guards (see the note above). The skill is"
  say "    installed; fix ~/.claude/settings.json and re-run to turn the guards on."
fi

# --- defaults (don't clobber an existing verbosity choice on re-install) --- #
mkdir -p "$CLAUDE_DIR/safe-start"
if [ ! -f "$CLAUDE_DIR/safe-start/config.json" ]; then
  printf '{\n  "verbosity": "teaching"\n}\n' > "$CLAUDE_DIR/safe-start/config.json"
fi

cat <<'EOF'

  ──────────────────────────────────────────────
  safe-start is on. Quietly, in every project, it:
    • warns before anything deletes work Git can't recover
    • warns before a real secret or patient identifier
      lands in a prompt or a commit
    • keeps the agent inside your project folder
    • greets you with where you are, and helps if Git tangles

  It never blocks you — it asks, and you decide.

  Remove anytime:  ~/.claude/skills/safe-start/uninstall.sh
  ──────────────────────────────────────────────
EOF
