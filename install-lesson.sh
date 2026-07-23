#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Install or update the clinician-first lesson for Claude Code and Codex.
set -euo pipefail
umask 077

DEFAULT_REF="v1.3.3"
PACKAGE_NAME="clinician-first-cli-session"
ARCHIVE_NAME="$PACKAGE_NAME.zip"
TARGET_MODE="${LESSON_TARGETS:-both}"

WORK_ROOT=""
EXTRACT_ROOT=""
COMMITTED=0
PRESERVE_BACKUPS=0
PREPARED_SKILLS=""
TARGET_LABELS=()
TARGET_SKILLS=()
TARGET_DESTS=()
TARGET_STAGES=()
TARGET_BACKUPS=()
TARGET_OLD_SAVED=()
TARGET_NEW_PLACED=()

say() { printf '%s\n' "$*"; }

lesson_tree_is_owned() {
  local dest="$1"
  [ -d "$dest" ] \
    && [ ! -L "$dest" ] \
    && [ -f "$dest/SKILL.md" ] \
    && [ ! -L "$dest/SKILL.md" ] \
    && [ -f "$dest/VERSION" ] \
    && [ ! -L "$dest/VERSION" ] \
    && [ -f "$dest/uninstall.sh" ] \
    && [ ! -L "$dest/uninstall.sh" ]
}

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  set +e

  if [ "$COMMITTED" -eq 0 ]; then
    for index in "${!TARGET_DESTS[@]}"; do
      dest="${TARGET_DESTS[$index]}"
      backup="${TARGET_BACKUPS[$index]:-}"
      if [ "${TARGET_NEW_PLACED[$index]:-0}" -eq 1 ] && [ -e "$dest" ]; then
        if lesson_tree_is_owned "$dest"; then
          rm -rf -- "$dest"
        else
          say "  ✗ ${TARGET_LABELS[$index]} rollback stopped because the new lesson path changed."
          status=1
          PRESERVE_BACKUPS=1
        fi
      fi
      if [ "${TARGET_OLD_SAVED[$index]:-0}" -eq 1 ]; then
        if [ ! -e "$dest" ] \
          && [ -d "$backup/$PACKAGE_NAME" ] \
          && [ ! -L "$backup/$PACKAGE_NAME" ]; then
          if mv -- "$backup/$PACKAGE_NAME" "$dest"; then
            say "  ✓ restored the previous ${TARGET_LABELS[$index]} lesson"
          else
            status=1
            PRESERVE_BACKUPS=1
          fi
        else
          status=1
          PRESERVE_BACKUPS=1
        fi
      fi
    done
  fi

  [ -z "$WORK_ROOT" ] || rm -rf -- "$WORK_ROOT"
  [ -z "$EXTRACT_ROOT" ] || rm -rf -- "$EXTRACT_ROOT"
  for stage in "${TARGET_STAGES[@]}"; do
    [ -z "$stage" ] || rm -rf -- "$stage"
  done
  if [ "$PRESERVE_BACKUPS" -eq 0 ]; then
    for backup in "${TARGET_BACKUPS[@]}"; do
      [ -z "$backup" ] || rm -rf -- "$backup"
    done
  else
    for backup in "${TARGET_BACKUPS[@]}"; do
      if [ -n "$backup" ] && [ -d "$backup" ]; then
        say "    Previous package backup preserved at $backup"
      fi
    done
  fi
  exit "$status"
}

trap cleanup EXIT HUP INT TERM

case "$TARGET_MODE" in
  both)
    TARGET_LABELS=("Claude Code" "Codex")
    TARGET_SKILLS=("${HOME:?HOME must be set}/.claude/skills" "$HOME/.agents/skills")
    ;;
  claude)
    TARGET_LABELS=("Claude Code")
    TARGET_SKILLS=("${HOME:?HOME must be set}/.claude/skills")
    ;;
  codex)
    TARGET_LABELS=("Codex")
    TARGET_SKILLS=("${HOME:?HOME must be set}/.agents/skills")
    ;;
  *)
    say "  ✗ LESSON_TARGETS must be claude, codex, or both."
    exit 1
    ;;
esac

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

EXTRACT_ROOT="$(mktemp -d)"
unzip -q "$ARCHIVE" -d "$EXTRACT_ROOT"
STAGED_SOURCE="$EXTRACT_ROOT/$PACKAGE_NAME"

if [ ! -d "$STAGED_SOURCE" ] || [ -L "$STAGED_SOURCE" ]; then
  say "  ✗ Lesson archive is missing its package directory."
  exit 1
fi
if find "$STAGED_SOURCE" ! -type f ! -type d -print -quit | grep -q .; then
  say "  ✗ Lesson package may contain only regular files and directories."
  exit 1
fi

REQUIRED_FILES=(
  "SKILL.md"
  "VERSION"
  "agents/openai.yaml"
  "uninstall.sh"
  "scripts/inspect_recovery.py"
  "scripts/restore_recovery.py"
  "references/basic-use-and-safety.md"
  "references/git-and-github-framework.md"
  "references/models-and-speed.md"
  "references/permissions-and-autonomy.md"
  "references/sessions-and-context.md"
  "references/teaching-aids.md"
  "references/tool-controls.md"
  "tests/test_contract.py"
  "tests/test_recovery.py"
)
for relative_path in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "$STAGED_SOURCE/$relative_path" ] \
    || [ -L "$STAGED_SOURCE/$relative_path" ]; then
    say "  ✗ Lesson package is missing $relative_path."
    exit 1
  fi
done

PACKAGE_VERSION="$(tr -d '\r\n' < "$STAGED_SOURCE/VERSION")"
if [[ ! "$PACKAGE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  say "  ✗ Lesson VERSION is not a semantic version."
  exit 1
fi
if [[ "$REF" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  && [ "v$PACKAGE_VERSION" != "$REF" ]; then
  say "  ✗ Release mismatch: $REF contains lesson $PACKAGE_VERSION."
  exit 1
fi

for index in "${!TARGET_SKILLS[@]}"; do
  logical_skills="${TARGET_SKILLS[$index]}"
  root_link="${logical_skills%/skills}"
  if [ -L "$root_link" ]; then
    if [ ! -d "$root_link" ]; then
      say "  ✗ $root_link must point to a real directory."
      exit 1
    fi
  elif [ -e "$root_link" ] && [ ! -d "$root_link" ]; then
    say "  ✗ $root_link must be a directory."
    exit 1
  elif [ ! -e "$root_link" ]; then
    mkdir -- "$root_link"
  fi
  if [ -L "$logical_skills" ]; then
    say "  ✗ $logical_skills must be a real directory, not a link."
    exit 1
  elif [ -e "$logical_skills" ] && [ ! -d "$logical_skills" ]; then
    say "  ✗ $logical_skills must be a directory."
    exit 1
  elif [ ! -e "$logical_skills" ]; then
    mkdir -- "$logical_skills"
  fi
  root_dir="$(CDPATH= cd -- "$root_link" && pwd -P)"
  skills_dir="$(CDPATH= cd -- "$logical_skills" && pwd -P)"
  if [ "$skills_dir" != "$root_dir/skills" ]; then
    say "  ✗ $logical_skills resolves outside its tool directory."
    exit 1
  fi
  TARGET_SKILLS[$index]="$skills_dir"
  dest="$skills_dir/$PACKAGE_NAME"
  if [ -L "$dest" ] || { [ -e "$dest" ] && [ ! -d "$dest" ]; }; then
    say "  ✗ $dest must be a real directory; refusing to replace it."
    exit 1
  fi
  TARGET_DESTS[$index]="$dest"
  TARGET_STAGES[$index]="$(mktemp -d "$skills_dir/.lesson.stage.XXXXXX")"
  TARGET_BACKUPS[$index]="$(mktemp -d "$skills_dir/.lesson.backup.XXXXXX")"
  TARGET_OLD_SAVED[$index]=0
  TARGET_NEW_PLACED[$index]=0
  cp -R -- "$STAGED_SOURCE" "${TARGET_STAGES[$index]}/$PACKAGE_NAME"
done

for index in "${!TARGET_DESTS[@]}"; do
  dest="${TARGET_DESTS[$index]}"
  backup="${TARGET_BACKUPS[$index]}"
  if [ -d "$dest" ]; then
    mv -- "$dest" "$backup/$PACKAGE_NAME"
    TARGET_OLD_SAVED[$index]=1
  fi
  mv -- "${TARGET_STAGES[$index]}/$PACKAGE_NAME" "$dest"
  TARGET_NEW_PLACED[$index]=1
done

COMMITTED=1
say "  ✓ clinician-first CLI lesson v$PACKAGE_VERSION installed"
for index in "${!TARGET_DESTS[@]}"; do
  say "    ${TARGET_LABELS[$index]}: ${TARGET_DESTS[$index]}"
done
say "    Claude Code: /clinician-first-cli-session"
say '    Codex:      $clinician-first-cli-session'
say "    No hooks or tool settings were changed."
say "    Remove both: bash ${TARGET_DESTS[0]}/uninstall.sh"
