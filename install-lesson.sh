#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Install or update the clinician-first CLI lesson without changing Claude
# settings or installing hooks.
set -euo pipefail
umask 077

DEFAULT_REF="v1.3.2"
PACKAGE_NAME="clinician-first-cli-session"
ARCHIVE_NAME="$PACKAGE_NAME.zip"
CLAUDE_DIR="${HOME:?HOME must be set}/.claude"
SKILLS_DIR="$CLAUDE_DIR/skills"
DEST="$SKILLS_DIR/$PACKAGE_NAME"

WORK_ROOT=""
STAGE_ROOT=""
BACKUP_ROOT=""
OLD_INSTALL_SAVED=0
NEW_INSTALL_PLACED=0
COMMITTED=0

say() { printf '%s\n' "$*"; }

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  set +e

  if [ "$COMMITTED" -eq 0 ]; then
    if [ "$NEW_INSTALL_PLACED" -eq 1 ] && [ -d "$DEST" ]; then
      rm -rf -- "$DEST"
    fi
    if [ "$OLD_INSTALL_SAVED" -eq 1 ] \
      && [ -d "$BACKUP_ROOT/$PACKAGE_NAME" ]; then
      if mv -- "$BACKUP_ROOT/$PACKAGE_NAME" "$DEST"; then
        say "  ✓ restored the previous lesson installation"
      else
        status=1
        say "  ✗ restore failed; the prior install remains at"
        say "    $BACKUP_ROOT/$PACKAGE_NAME"
        BACKUP_ROOT=""
      fi
    fi
  fi

  [ -z "$WORK_ROOT" ] || rm -rf -- "$WORK_ROOT"
  [ -z "$STAGE_ROOT" ] || rm -rf -- "$STAGE_ROOT"
  [ -z "$BACKUP_ROOT" ] || rm -rf -- "$BACKUP_ROOT"
  exit "$status"
}

trap cleanup EXIT HUP INT TERM

for command_name in unzip shasum; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    say "  ✗ $command_name is required to install the lesson."
    exit 1
  fi
done

REF="${LESSON_REF:-$DEFAULT_REF}"
case "$REF" in
  ""|-*)
    say "  ✗ LESSON_REF must be a non-empty Git ref, not an option."
    exit 1
    ;;
esac

WORK_ROOT="$(mktemp -d)"
ARCHIVE="$WORK_ROOT/$ARCHIVE_NAME"
CHECKSUMS="$WORK_ROOT/SHA256SUMS"

if [ -n "${LESSON_ARCHIVE:-}" ] || [ -n "${LESSON_CHECKSUMS:-}" ]; then
  if [ -z "${LESSON_ARCHIVE:-}" ] || [ -z "${LESSON_CHECKSUMS:-}" ]; then
    say "  ✗ Set both LESSON_ARCHIVE and LESSON_CHECKSUMS for a local install."
    exit 1
  fi
  cp -- "$LESSON_ARCHIVE" "$ARCHIVE"
  cp -- "$LESSON_CHECKSUMS" "$CHECKSUMS"
else
  if ! command -v curl >/dev/null 2>&1; then
    say "  ✗ curl is required for a remote install."
    exit 1
  fi
  BASE_URL="${LESSON_BASE_URL:-https://raw.githubusercontent.com/dochobbs/OC_Safe_start/$REF}"
  say "Fetching clinician-first CLI lesson ${REF}..."
  curl -fsSL "$BASE_URL/$ARCHIVE_NAME" -o "$ARCHIVE"
  curl -fsSL "$BASE_URL/SHA256SUMS" -o "$CHECKSUMS"
fi

EXPECTED="$(awk -v name="$ARCHIVE_NAME" '$2 == name { print $1 }' "$CHECKSUMS")"
if ! printf '%s' "$EXPECTED" | grep -Eq '^[0-9a-fA-F]{64}$'; then
  say "  ✗ No valid checksum was published for $ARCHIVE_NAME."
  exit 1
fi
ACTUAL="$(shasum -a 256 "$ARCHIVE" | awk '{ print $1 }')"
if [ "$ACTUAL" != "$EXPECTED" ]; then
  say "  ✗ Lesson archive checksum mismatch; nothing was installed."
  exit 1
fi

if unzip -Z1 "$ARCHIVE" | awk '
  /^\// || /(^|\/)\.\.($|\/)/ || /\\/ { bad=1 }
  END { exit bad ? 0 : 1 }
'; then
  say "  ✗ Lesson archive contains an unsafe path."
  exit 1
fi

mkdir -p -- "$SKILLS_DIR"
STAGE_ROOT="$(mktemp -d "$SKILLS_DIR/.lesson.stage.XXXXXX")"
unzip -q "$ARCHIVE" -d "$STAGE_ROOT"
STAGED_DEST="$STAGE_ROOT/$PACKAGE_NAME"

if [ ! -d "$STAGED_DEST" ] || [ -L "$STAGED_DEST" ]; then
  say "  ✗ Lesson archive is missing its package directory."
  exit 1
fi
if find "$STAGED_DEST" ! -type f ! -type d -print -quit | grep -q .; then
  say "  ✗ Lesson package may contain only regular files and directories."
  exit 1
fi

REQUIRED_FILES=(
  "SKILL.md"
  "VERSION"
  "uninstall.sh"
  "scripts/inspect_recovery.py"
  "scripts/restore_recovery.py"
  "references/basic-use-and-safety.md"
  "references/git-and-github-framework.md"
  "references/models-and-speed.md"
  "references/permissions-and-autonomy.md"
  "references/sessions-and-context.md"
  "references/teaching-aids.md"
  "tests/test_contract.py"
  "tests/test_recovery.py"
)
for relative_path in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "$STAGED_DEST/$relative_path" ] \
    || [ -L "$STAGED_DEST/$relative_path" ]; then
    say "  ✗ Lesson package is missing $relative_path."
    exit 1
  fi
done

PACKAGE_VERSION="$(tr -d '\r\n' < "$STAGED_DEST/VERSION")"
if [[ ! "$PACKAGE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  say "  ✗ Lesson VERSION is not a semantic version."
  exit 1
fi
if [[ "$REF" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  && [ "v$PACKAGE_VERSION" != "$REF" ]; then
  say "  ✗ Release mismatch: $REF contains lesson $PACKAGE_VERSION."
  exit 1
fi

if [ -L "$DEST" ] || { [ -e "$DEST" ] && [ ! -d "$DEST" ]; }; then
  say "  ✗ $DEST must be a real directory; refusing to replace it."
  exit 1
fi

BACKUP_ROOT="$(mktemp -d "$SKILLS_DIR/.lesson.backup.XXXXXX")"
if [ -d "$DEST" ]; then
  mv -- "$DEST" "$BACKUP_ROOT/$PACKAGE_NAME"
  OLD_INSTALL_SAVED=1
fi
mv -- "$STAGED_DEST" "$DEST"
NEW_INSTALL_PLACED=1
COMMITTED=1

say "  ✓ clinician-first CLI lesson v$PACKAGE_VERSION installed"
say "    $DEST"
say "    No hooks or Claude settings were changed."
say "    Remove: bash $DEST/uninstall.sh"
