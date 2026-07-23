#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Remove the clinician-first lesson from Claude Code and Codex.
set -euo pipefail

PACKAGE_NAME="clinician-first-cli-session"
TARGET_MODE="${LESSON_TARGETS:-both}"
DESTS=()
LABELS=()

case "$TARGET_MODE" in
  both)
    LABELS=("Claude Code" "Codex")
    DESTS=(
      "${HOME:?HOME must be set}/.claude/skills/$PACKAGE_NAME"
      "$HOME/.agents/skills/$PACKAGE_NAME"
    )
    ;;
  claude)
    LABELS=("Claude Code")
    DESTS=("${HOME:?HOME must be set}/.claude/skills/$PACKAGE_NAME")
    ;;
  codex)
    LABELS=("Codex")
    DESTS=("${HOME:?HOME must be set}/.agents/skills/$PACKAGE_NAME")
    ;;
  *)
    printf '%s\n' "  ✗ LESSON_TARGETS must be claude, codex, or both."
    exit 1
    ;;
esac

for dest in "${DESTS[@]}"; do
  if [ -L "$dest" ] || { [ -e "$dest" ] && [ ! -d "$dest" ]; }; then
    printf '  ✗ %s is not an owned lesson directory; refusing to remove it or the other tool copy.\n' "$dest"
    exit 1
  fi
  if [ -d "$dest" ] \
    && { [ ! -f "$dest/SKILL.md" ] || [ ! -f "$dest/VERSION" ] \
      || [ ! -f "$dest/uninstall.sh" ]; }; then
    printf '  ✗ %s does not look like the installed lesson; refusing to remove it or the other tool copy.\n' "$dest"
    exit 1
  fi
done

removed=0
for index in "${!DESTS[@]}"; do
  dest="${DESTS[$index]}"
  if [ -d "$dest" ]; then
    rm -rf -- "$dest"
    printf '  ✓ clinician-first CLI lesson removed from %s\n' "${LABELS[$index]}"
    removed=1
  fi
done

if [ "$removed" -eq 0 ]; then
  printf '  ✓ clinician-first CLI lesson is already absent\n'
fi
printf '    No hooks or tool settings were changed.\n'
