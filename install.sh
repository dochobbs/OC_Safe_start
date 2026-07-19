#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Install or update safe-start without exposing a half-installed safety net.
#
#   curl -fsSL <url>/install.sh | bash
#
# Remote installs are pinned to a release tag.  Developers may explicitly set
# SAFE_START_SRC to test a local checkout.
set -euo pipefail
umask 077

CLAUDE_DIR="${HOME:?HOME must be set}/.claude"
SKILLS_DIR="$CLAUDE_DIR/skills"
DEST="$SKILLS_DIR/safe-start"
STATE_DIR="$CLAUDE_DIR/safe-start"
CONFIG="$STATE_DIR/config.json"
DEFAULT_REF="v1.1.0"

STAGE_ROOT=""
STAGED_DEST=""
CLONE_ROOT=""
BACKUP_ROOT=""
CONFIG_TMP=""
TRANSACTION_OPEN=0
OLD_INSTALL_SAVED=0
NEW_INSTALL_PLACED=0
REGISTRATION_IN_PROGRESS=0
COMMITTED=0
STATE_CREATED=0
CONFIG_CREATED=0
PRESERVE_BACKUP=0

say() { printf '%s\n' "$*"; }
ignore_interrupts() { trap '' HUP INT TERM; }
restore_interrupts() { trap 'exit 130' HUP INT TERM; }

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  set +e

  if [ "$REGISTRATION_IN_PROGRESS" -eq 1 ]; then
    PRESERVE_BACKUP=1
    say "  ✗ installation was interrupted while settings were being updated."
    say "    The new hook files and previous-install backup were preserved;"
    say "    re-run the installer to converge safely."
  elif [ "$TRANSACTION_OPEN" -eq 1 ] && [ "$COMMITTED" -eq 0 ]; then
    if [ "$NEW_INSTALL_PLACED" -eq 1 ] \
      && { [ -e "$DEST" ] || [ -L "$DEST" ]; }; then
      rm -rf -- "$DEST"
    fi
    if [ "$OLD_INSTALL_SAVED" -eq 1 ] \
      && [ -d "$BACKUP_ROOT/safe-start" ]; then
      if mv -- "$BACKUP_ROOT/safe-start" "$DEST"; then
        say "  ✓ restored the previous safe-start installation"
      else
        PRESERVE_BACKUP=1
        status=1
        say "  ✗ automatic restore failed; the previous installation is"
        say "    preserved at $BACKUP_ROOT/safe-start"
      fi
    fi
  fi

  if [ "$COMMITTED" -eq 0 ] && [ "$REGISTRATION_IN_PROGRESS" -eq 0 ]; then
    if [ "$CONFIG_CREATED" -eq 1 ]; then
      rm -f -- "$CONFIG"
    fi
    if [ "$STATE_CREATED" -eq 1 ]; then
      rmdir -- "$STATE_DIR" 2>/dev/null
    fi
  fi

  if [ -n "$CONFIG_TMP" ]; then
    rm -f -- "$CONFIG_TMP"
  fi
  if [ -n "$STAGE_ROOT" ]; then
    rm -rf -- "$STAGE_ROOT"
  fi
  if [ -n "$CLONE_ROOT" ]; then
    rm -rf -- "$CLONE_ROOT"
  fi
  if [ -n "$BACKUP_ROOT" ] && [ "$PRESERVE_BACKUP" -eq 0 ]; then
    rm -rf -- "$BACKUP_ROOT"
  fi
  exit "$status"
}

trap cleanup EXIT
restore_interrupts

say "Installing safe-start…"

# --- prerequisites -------------------------------------------------------- #
if ! command -v python3 >/dev/null 2>&1; then
  say "  ✗ Python 3 is required (it ships with the macOS developer tools:"
  say "    run 'xcode-select --install', then try again)."
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
  say "  ✗ safe-start requires Python 3.9 or newer."
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  say "  ✗ git is required (run 'xcode-select --install', then try again)."
  exit 1
fi

# --- locate an explicit local source, a real adjacent file, or a pinned tag #
# Do not fall back to $0 or the current directory: when this script is piped to
# bash, those identify bash/the caller's CWD rather than the downloaded file.
SCRIPT_FILE="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [ -n "$SCRIPT_FILE" ] && [ -f "$SCRIPT_FILE" ]; then
  SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$SCRIPT_FILE")" && pwd -P)"
fi

REMOTE_SOURCE=0
if [ -n "${SAFE_START_SRC:-}" ]; then
  if [ ! -d "$SAFE_START_SRC" ]; then
    say "  ✗ SAFE_START_SRC is not a directory: $SAFE_START_SRC"
    exit 1
  fi
  SRC="$(CDPATH= cd -- "$SAFE_START_SRC" && pwd -P)"
elif [ -n "$SCRIPT_DIR" ] \
  && [ -f "$SCRIPT_DIR/SKILL.md" ] \
  && [ -f "$SCRIPT_DIR/hooks/pretooluse.py" ]; then
  SRC="$SCRIPT_DIR"
else
  REPO_URL="${SAFE_START_REPO:-https://github.com/dochobbs/OC_Safe_start.git}"
  REPO_REF="${SAFE_START_REF:-$DEFAULT_REF}"
  case "$REPO_REF" in
    ""|-*)
      say "  ✗ SAFE_START_REF must be a non-empty Git ref, not an option."
      exit 1
      ;;
  esac
  CLONE_ROOT="$(mktemp -d)"
  SRC="$CLONE_ROOT"
  REMOTE_SOURCE=1
  say "  Fetching safe-start ${REPO_REF}…"
  if ! git clone --depth 1 --branch "$REPO_REF" -- "$REPO_URL" "$SRC" \
    >/dev/null 2>&1; then
    say "  ✗ Could not fetch safe-start $REPO_REF from $REPO_URL"
    say "    Set SAFE_START_SRC to an explicit local copy and re-run."
    exit 1
  fi
  if ! HEAD_COMMIT="$(git -C "$SRC" rev-parse --verify HEAD 2>/dev/null)" \
    || ! REF_COMMIT="$(git -C "$SRC" rev-parse --verify "${REPO_REF}^{commit}" \
      2>/dev/null)" \
    || [ -z "$HEAD_COMMIT" ] \
    || [ "$HEAD_COMMIT" != "$REF_COMMIT" ]; then
    say "  ✗ The fetched checkout does not resolve to $REPO_REF; refusing it."
    exit 1
  fi
fi

# --- stage and validate without touching the live installation ------------ #
mkdir -p -- "$SKILLS_DIR"
STAGE_ROOT="$(mktemp -d "$SKILLS_DIR/.safe-start.stage.XXXXXX")"
STAGED_DEST="$STAGE_ROOT/safe-start"
mkdir -- "$STAGED_DEST"

if ! (cd -- "$SRC" \
  && tar --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' -cf - .) \
  | (cd -- "$STAGED_DEST" && tar -xf -); then
  say "  ✗ Could not stage the safe-start files; the live install is unchanged."
  exit 1
fi

# A package is executable code. Refuse links, devices, sockets, and other
# entries whose eventual target or behavior is not contained in the reviewed
# package bytes.
if find "$STAGED_DEST" ! -type f ! -type d -print -quit | grep -q .; then
  say "  ✗ Source validation failed: package entries must be regular files or directories."
  exit 1
fi

REQUIRED_FILES=(
  "LICENSE"
  "SKILL.md"
  "VERSION"
  "install.sh"
  "uninstall.sh"
  "hooks/pretooluse.py"
  "hooks/userpromptsubmit.py"
  "hooks/sessionstart.py"
  "install/merge_settings.py"
  "tests/test_detectors.py"
  "tests/test_hooks.py"
  "tests/test_lifecycle.py"
)
for relative_path in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "$STAGED_DEST/$relative_path" ] \
    || [ -L "$STAGED_DEST/$relative_path" ]; then
    say "  ✗ Source validation failed: missing regular file $relative_path"
    exit 1
  fi
done

PACKAGE_VERSION="$(tr -d '\r\n' < "$STAGED_DEST/VERSION")"
if [[ ! "$PACKAGE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  say "  ✗ VERSION must contain a semantic version such as 1.1.0."
  exit 1
fi
if [ "$REMOTE_SOURCE" -eq 1 ]; then
  if [[ "$REPO_REF" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    && [ "v$PACKAGE_VERSION" != "$REPO_REF" ]; then
    say "  ✗ Release mismatch: $REPO_REF contains VERSION $PACKAGE_VERSION."
    exit 1
  fi
fi

chmod 755 "$STAGED_DEST/install.sh" "$STAGED_DEST/uninstall.sh"
if ! bash -n "$STAGED_DEST/install.sh" "$STAGED_DEST/uninstall.sh" \
  || ! PYTHONDONTWRITEBYTECODE=1 python3 -c \
    'import pathlib, sys; compile(pathlib.Path(sys.argv[1]).read_text(), sys.argv[1], "exec")' \
    "$STAGED_DEST/install/merge_settings.py"; then
  say "  ✗ Installer validation failed; the live install is unchanged."
  exit 1
fi

TEST_LOG="$STAGE_ROOT/self-test.log"
if ! PYTHONDONTWRITEBYTECODE=1 python3 "$STAGED_DEST/tests/test_detectors.py" \
    >"$TEST_LOG" 2>&1 \
  || ! PYTHONDONTWRITEBYTECODE=1 python3 "$STAGED_DEST/tests/test_hooks.py" \
    >>"$TEST_LOG" 2>&1; then
  say "  ✗ Self-test failed; the live install is unchanged."
  cat "$TEST_LOG" >&2
  exit 1
fi
say "  ✓ detection + hook self-test passed"

# --- prepare private state before opening the install transaction ---------- #
if [ -L "$STATE_DIR" ] \
  || { [ -e "$STATE_DIR" ] && [ ! -d "$STATE_DIR" ]; }; then
  say "  ✗ $STATE_DIR must be a real directory, not a link or file."
  exit 1
fi
if [ ! -d "$STATE_DIR" ]; then
  mkdir -- "$STATE_DIR"
  STATE_CREATED=1
fi
chmod 700 "$STATE_DIR"

if [ -L "$CONFIG" ] \
  || { [ -e "$CONFIG" ] && [ ! -f "$CONFIG" ]; }; then
  say "  ✗ $CONFIG must be a regular file, not a link."
  exit 1
fi
if [ -f "$CONFIG" ]; then
  chmod 600 "$CONFIG"
else
  CONFIG_TMP="$(mktemp "$STATE_DIR/.config.json.XXXXXX")"
  chmod 600 "$CONFIG_TMP"
  printf '{\n  "verbosity": "teaching"\n}\n' > "$CONFIG_TMP"
  mv -- "$CONFIG_TMP" "$CONFIG"
  CONFIG_TMP=""
  CONFIG_CREATED=1
fi

# --- atomically replace the live skill, then register and verify its hooks -- #
if [ -L "$DEST" ] || { [ -e "$DEST" ] && [ ! -d "$DEST" ]; }; then
  say "  ✗ $DEST must be a real directory; refusing to replace it."
  exit 1
fi

BACKUP_ROOT="$(mktemp -d "$SKILLS_DIR/.safe-start.backup.XXXXXX")"
TRANSACTION_OPEN=1

# A rename and its state flag form one logical step. Ignore catchable signals
# only across these tiny windows so cleanup never mistakes the old install for
# the new one or loses track of the backup.
ignore_interrupts
if [ -d "$DEST" ]; then
  if ! mv -- "$DEST" "$BACKUP_ROOT/safe-start"; then
    restore_interrupts
    say "  ✗ Could not preserve the previous installation; nothing was replaced."
    exit 1
  fi
  OLD_INSTALL_SAVED=1
fi
if ! mv -- "$STAGED_DEST" "$DEST"; then
  restore_interrupts
  say "  ✗ Could not activate the staged installation; rolling back."
  exit 1
fi
NEW_INSTALL_PLACED=1
restore_interrupts

# If interrupted while the transactional settings merger is running, cleanup
# preserves both the new scripts and the old backup. That avoids dangling hook
# commands while still allowing a blocked lock acquisition to be interrupted.
REGISTRATION_IN_PROGRESS=1
if ! python3 "$DEST/install/merge_settings.py" add "$DEST/hooks"; then
  REGISTRATION_IN_PROGRESS=0
  say "  ✗ Guard registration failed; the new installation will be rolled back."
  exit 1
fi
COMMITTED=1
REGISTRATION_IN_PROGRESS=0

say "  ✓ guards registered and verified in $CLAUDE_DIR/settings.json"

cat <<EOF

  ──────────────────────────────────────────────
  safe-start v$PACKAGE_VERSION is on. Quietly, in every project, it:
    • asks before covered high-blast-radius delete/history commands
    • rejects a prompt when it detects a secret or structured
      patient identifier, and checks Git candidates too
    • asks before direct paths leave your project; this is advisory,
      not a sandbox
    • greets you with where you are, and helps if Git tangles

  Remove anytime:  ~/.claude/skills/safe-start/uninstall.sh
  ──────────────────────────────────────────────
EOF
