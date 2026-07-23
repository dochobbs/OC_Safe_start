#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Install the stable clinician-first lesson and safe-start net with one command.
set -euo pipefail
umask 077

SAFE_SYSTEM_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
export PATH="$SAFE_SYSTEM_PATH"
unset PYTHONHOME PYTHONINSPECT PYTHONPATH PYTHONSTARTUP
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1

BUNDLE_VERSION="1.0.3"
LESSON_REF="v1.3.3"
SAFE_START_REF="v1.1.0"
RAW_ROOT="https://raw.githubusercontent.com/dochobbs/OC_Safe_start"
PACKAGE_NAME="clinician-first-cli-session"

CLAUDE_LINK="${HOME:?HOME must be set}/.claude"
CODEX_LINK="$HOME/.agents"
CLAUDE_DIR=""
SKILLS_DIR=""
LESSON_DEST=""
CODEX_DIR=""
CODEX_SKILLS_DIR=""
CODEX_LESSON_DEST=""
WORK_ROOT=""
BACKUP_ROOT=""
CODEX_BACKUP_ROOT=""
OLD_LESSON_SAVED=0
OLD_CODEX_LESSON_SAVED=0
NEW_LESSON_INSTALLED=0
COMMITTED=0
PATHS_PREPARED=0

say() { printf '%s\n' "$*"; }

managed_roots_are_safe() {
  [ -n "$CLAUDE_DIR" ] \
    && [ -d "$CLAUDE_DIR" ] \
    && [ ! -L "$CLAUDE_DIR" ] \
    && [ -d "$SKILLS_DIR" ] \
    && [ ! -L "$SKILLS_DIR" ] \
    && [ "$SKILLS_DIR" = "$CLAUDE_DIR/skills" ] \
    && [ "$(CDPATH= cd -- "$CLAUDE_DIR" 2>/dev/null && pwd -P)" = "$CLAUDE_DIR" ] \
    && [ "$(CDPATH= cd -- "$SKILLS_DIR" 2>/dev/null && pwd -P)" = "$SKILLS_DIR" ] \
    && [ -n "$CODEX_DIR" ] \
    && [ -d "$CODEX_DIR" ] \
    && [ ! -L "$CODEX_DIR" ] \
    && [ -d "$CODEX_SKILLS_DIR" ] \
    && [ ! -L "$CODEX_SKILLS_DIR" ] \
    && [ "$CODEX_SKILLS_DIR" = "$CODEX_DIR/skills" ] \
    && [ "$(CDPATH= cd -- "$CODEX_DIR" 2>/dev/null && pwd -P)" = "$CODEX_DIR" ] \
    && [ "$(CDPATH= cd -- "$CODEX_SKILLS_DIR" 2>/dev/null && pwd -P)" = "$CODEX_SKILLS_DIR" ]
}

lesson_tree_is_owned() {
  local dest="$1"
  local skills="$2"
  [ -d "$dest" ] \
    && [ ! -L "$dest" ] \
    && [ "${dest%/*}" = "$skills" ] \
    && [ -f "$dest/SKILL.md" ] \
    && [ ! -L "$dest/SKILL.md" ] \
    && [ -f "$dest/VERSION" ] \
    && [ ! -L "$dest/VERSION" ] \
    && [ -f "$dest/uninstall.sh" ] \
    && [ ! -L "$dest/uninstall.sh" ]
}

restore_lesson() {
  local failed=0
  if ! managed_roots_are_safe; then
    say "  ✗ Lesson rollback stopped because the Claude install paths changed."
    return 1
  fi

  if [ "$NEW_LESSON_INSTALLED" -eq 1 ] && [ -e "$LESSON_DEST" ]; then
    if lesson_tree_is_owned "$LESSON_DEST" "$SKILLS_DIR"; then
      rm -rf -- "$LESSON_DEST"
    else
      say "  ✗ The new lesson path changed; it was preserved for inspection."
      failed=1
    fi
  fi

  if [ "$NEW_LESSON_INSTALLED" -eq 1 ] && [ -e "$CODEX_LESSON_DEST" ]; then
    if lesson_tree_is_owned "$CODEX_LESSON_DEST" "$CODEX_SKILLS_DIR"; then
      rm -rf -- "$CODEX_LESSON_DEST"
    else
      say "  ✗ The new Codex lesson path changed; it was preserved for inspection."
      failed=1
    fi
  fi

  if [ "$OLD_LESSON_SAVED" -eq 1 ]; then
    if [ "$failed" -eq 0 ] \
      && [ ! -e "$LESSON_DEST" ] \
      && [ -d "$BACKUP_ROOT/$PACKAGE_NAME" ] \
      && [ ! -L "$BACKUP_ROOT/$PACKAGE_NAME" ]; then
      mv -- "$BACKUP_ROOT/$PACKAGE_NAME" "$LESSON_DEST"
      OLD_LESSON_SAVED=0
      say "  ✓ restored the previous lesson installation"
    else
      say "  ✗ The previous lesson remains preserved at"
      say "    $BACKUP_ROOT/$PACKAGE_NAME"
      failed=1
    fi
  fi
  if [ "$OLD_CODEX_LESSON_SAVED" -eq 1 ]; then
    if [ "$failed" -eq 0 ] \
      && [ ! -e "$CODEX_LESSON_DEST" ] \
      && [ -d "$CODEX_BACKUP_ROOT/$PACKAGE_NAME" ] \
      && [ ! -L "$CODEX_BACKUP_ROOT/$PACKAGE_NAME" ]; then
      mv -- "$CODEX_BACKUP_ROOT/$PACKAGE_NAME" "$CODEX_LESSON_DEST"
      OLD_CODEX_LESSON_SAVED=0
      say "  ✓ restored the previous Codex lesson installation"
    else
      say "  ✗ The previous Codex lesson remains preserved at"
      say "    $CODEX_BACKUP_ROOT/$PACKAGE_NAME"
      failed=1
    fi
  fi
  return "$failed"
}

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  set +e

  if [ "$COMMITTED" -eq 0 ] && [ "$PATHS_PREPARED" -eq 1 ]; then
    if ! restore_lesson; then
      status=1
      BACKUP_ROOT=""
      CODEX_BACKUP_ROOT=""
    fi
  fi

  if [ -n "$WORK_ROOT" ] && [ -d "$WORK_ROOT" ]; then
    rm -rf -- "$WORK_ROOT"
  fi
  if [ -n "$BACKUP_ROOT" ] && [ -d "$BACKUP_ROOT" ]; then
    rm -rf -- "$BACKUP_ROOT"
  fi
  if [ -n "$CODEX_BACKUP_ROOT" ] && [ -d "$CODEX_BACKUP_ROOT" ]; then
    rm -rf -- "$CODEX_BACKUP_ROOT"
  fi
  exit "$status"
}

trap cleanup EXIT HUP INT TERM

prepare_paths() {
  local logical_skills
  if [ -L "$CLAUDE_LINK" ]; then
    if [ ! -d "$CLAUDE_LINK" ]; then
      say "  ✗ $CLAUDE_LINK must point to a real directory."
      return 1
    fi
  elif [ -e "$CLAUDE_LINK" ] && [ ! -d "$CLAUDE_LINK" ]; then
    say "  ✗ $CLAUDE_LINK must be a directory."
    return 1
  elif [ ! -e "$CLAUDE_LINK" ]; then
    mkdir -- "$CLAUDE_LINK"
  fi

  logical_skills="$CLAUDE_LINK/skills"
  if [ -L "$logical_skills" ]; then
    say "  ✗ $logical_skills must be a real directory, not a link."
    return 1
  elif [ -e "$logical_skills" ] && [ ! -d "$logical_skills" ]; then
    say "  ✗ $logical_skills must be a directory."
    return 1
  elif [ ! -e "$logical_skills" ]; then
    mkdir -- "$logical_skills"
  fi

  CLAUDE_DIR="$(CDPATH= cd -- "$CLAUDE_LINK" && pwd -P)"
  SKILLS_DIR="$(CDPATH= cd -- "$logical_skills" && pwd -P)"
  if [ "$SKILLS_DIR" != "$CLAUDE_DIR/skills" ]; then
    say "  ✗ $logical_skills resolves outside the Claude directory."
    return 1
  fi
  LESSON_DEST="$SKILLS_DIR/$PACKAGE_NAME"
  if [ -L "$LESSON_DEST" ] \
    || { [ -e "$LESSON_DEST" ] && [ ! -d "$LESSON_DEST" ]; }; then
    say "  ✗ $LESSON_DEST must be a real directory; refusing to replace it."
    return 1
  fi

  if [ -L "$CODEX_LINK" ]; then
    if [ ! -d "$CODEX_LINK" ]; then
      say "  ✗ $CODEX_LINK must point to a real directory."
      return 1
    fi
  elif [ -e "$CODEX_LINK" ] && [ ! -d "$CODEX_LINK" ]; then
    say "  ✗ $CODEX_LINK must be a directory."
    return 1
  elif [ ! -e "$CODEX_LINK" ]; then
    mkdir -- "$CODEX_LINK"
  fi

  logical_skills="$CODEX_LINK/skills"
  if [ -L "$logical_skills" ]; then
    say "  ✗ $logical_skills must be a real directory, not a link."
    return 1
  elif [ -e "$logical_skills" ] && [ ! -d "$logical_skills" ]; then
    say "  ✗ $logical_skills must be a directory."
    return 1
  elif [ ! -e "$logical_skills" ]; then
    mkdir -- "$logical_skills"
  fi

  CODEX_DIR="$(CDPATH= cd -- "$CODEX_LINK" && pwd -P)"
  CODEX_SKILLS_DIR="$(CDPATH= cd -- "$logical_skills" && pwd -P)"
  if [ "$CODEX_SKILLS_DIR" != "$CODEX_DIR/skills" ]; then
    say "  ✗ $logical_skills resolves outside the Codex skill directory."
    return 1
  fi
  CODEX_LESSON_DEST="$CODEX_SKILLS_DIR/$PACKAGE_NAME"
  if [ -L "$CODEX_LESSON_DEST" ] \
    || { [ -e "$CODEX_LESSON_DEST" ] && [ ! -d "$CODEX_LESSON_DEST" ]; }; then
    say "  ✗ $CODEX_LESSON_DEST must be a real directory; refusing to replace it."
    return 1
  fi
  PATHS_PREPARED=1
}

regular_script() {
  [ -f "$1" ] && [ ! -L "$1" ]
}

say "Installing Offcall starter bundle v${BUNDLE_VERSION}…"

if [ -n "${OFFCALL_BUNDLE_SOURCE:-}" ]; then
  if [ ! -d "$OFFCALL_BUNDLE_SOURCE" ]; then
    say "  ✗ OFFCALL_BUNDLE_SOURCE is not a directory."
    exit 1
  fi
  SOURCE_ROOT="$(CDPATH= cd -- "$OFFCALL_BUNDLE_SOURCE" && pwd -P)"
  LESSON_INSTALLER="${OFFCALL_BUNDLE_LESSON_INSTALLER:-$SOURCE_ROOT/skills/clinician-first-cli-session/install.sh}"
  SAFE_START_INSTALLER="${OFFCALL_BUNDLE_SAFE_START_INSTALLER:-$SOURCE_ROOT/skills/safe-start/install.sh}"
  LESSON_ARCHIVE="$SOURCE_ROOT/dist/clinician-first-cli-session.zip"
  CHECKSUMS="$SOURCE_ROOT/dist/SHA256SUMS"
  SAFE_START_SOURCE="$SOURCE_ROOT/skills/safe-start"
  LESSON_REF="v$(/usr/bin/tr -d '\r\n' < "$SOURCE_ROOT/skills/clinician-first-cli-session/VERSION")"
  SAFE_START_REF="v$(/usr/bin/tr -d '\r\n' < "$SOURCE_ROOT/skills/safe-start/VERSION")"
else
  WORK_ROOT="$(mktemp -d)"
  LESSON_INSTALLER="$WORK_ROOT/install-lesson.sh"
  SAFE_START_INSTALLER="$WORK_ROOT/install-safe-start.sh"
  LESSON_ARCHIVE="$WORK_ROOT/clinician-first-cli-session.zip"
  CHECKSUMS="$WORK_ROOT/SHA256SUMS"
  SAFE_START_SOURCE=""

  /usr/bin/curl -fsSL "$RAW_ROOT/$LESSON_REF/install-lesson.sh" -o "$LESSON_INSTALLER"
  /usr/bin/curl -fsSL "$RAW_ROOT/$LESSON_REF/clinician-first-cli-session.zip" -o "$LESSON_ARCHIVE"
  /usr/bin/curl -fsSL "$RAW_ROOT/$LESSON_REF/SHA256SUMS" -o "$CHECKSUMS"
  /usr/bin/curl -fsSL "$RAW_ROOT/$SAFE_START_REF/install.sh" -o "$SAFE_START_INSTALLER"
fi

if ! regular_script "$LESSON_INSTALLER" \
  || ! regular_script "$SAFE_START_INSTALLER" \
  || [ ! -f "$LESSON_ARCHIVE" ] \
  || [ -L "$LESSON_ARCHIVE" ] \
  || [ ! -f "$CHECKSUMS" ] \
  || [ -L "$CHECKSUMS" ]; then
  say "  ✗ The pinned installer bundle is incomplete; nothing was installed."
  exit 1
fi

EXPECTED="$(/usr/bin/awk -v name="${LESSON_ARCHIVE##*/}" '$2 == name { print $1 }' "$CHECKSUMS")"
if ! printf '%s' "$EXPECTED" | /usr/bin/grep -Eq '^[0-9a-fA-F]{64}$'; then
  say "  ✗ The lesson checksum is missing; nothing was installed."
  exit 1
fi
ACTUAL="$(/usr/bin/shasum -a 256 "$LESSON_ARCHIVE" | /usr/bin/awk '{ print $1 }')"
if [ "$ACTUAL" != "$EXPECTED" ]; then
  say "  ✗ The lesson checksum does not match; nothing was installed."
  exit 1
fi

if ! prepare_paths; then
  exit 1
fi
BACKUP_ROOT="$(mktemp -d "$SKILLS_DIR/.offcall-bundle.backup.XXXXXX")"
CODEX_BACKUP_ROOT="$(mktemp -d "$CODEX_SKILLS_DIR/.offcall-bundle.backup.XXXXXX")"
if [ -d "$LESSON_DEST" ]; then
  mv -- "$LESSON_DEST" "$BACKUP_ROOT/$PACKAGE_NAME"
  OLD_LESSON_SAVED=1
fi
if [ -d "$CODEX_LESSON_DEST" ]; then
  mv -- "$CODEX_LESSON_DEST" "$CODEX_BACKUP_ROOT/$PACKAGE_NAME"
  OLD_CODEX_LESSON_SAVED=1
fi

if ! LESSON_ARCHIVE="$LESSON_ARCHIVE" \
  LESSON_CHECKSUMS="$CHECKSUMS" \
  LESSON_REF="$LESSON_REF" \
  /bin/bash "$LESSON_INSTALLER"; then
  say "  ✗ The lesson did not install; safe-start was not changed."
  exit 1
fi
NEW_LESSON_INSTALLED=1

if [ -n "$SAFE_START_SOURCE" ]; then
  if ! SAFE_START_SRC="$SAFE_START_SOURCE" \
    SAFE_START_REF="$SAFE_START_REF" \
    /bin/bash "$SAFE_START_INSTALLER"; then
    say "  ✗ safe-start did not install; the lesson will be rolled back."
    exit 1
  fi
else
  if ! SAFE_START_REF="$SAFE_START_REF" /bin/bash "$SAFE_START_INSTALLER"; then
    say "  ✗ safe-start did not install; the lesson will be rolled back."
    exit 1
  fi
fi

COMMITTED=1
say ""
say "  ✓ Offcall is ready for Claude Code and Codex"
say "    Lesson:    clinician-first-cli-session $LESSON_REF (both tools)"
say "    Safety net: safe-start $SAFE_START_REF (Claude Code only)"
say "    Claude Code: /clinician-first-cli-session"
say '    Codex:      $clinician-first-cli-session'
say "    Restart either tool if the lesson does not appear immediately."
