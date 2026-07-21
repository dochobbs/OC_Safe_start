#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Remove only the clinician-first CLI lesson package.
set -euo pipefail

PACKAGE_NAME="clinician-first-cli-session"
DEST="${HOME:?HOME must be set}/.claude/skills/$PACKAGE_NAME"

if [ -L "$DEST" ] || { [ -e "$DEST" ] && [ ! -d "$DEST" ]; }; then
  printf '  ✗ %s is not an owned lesson directory; refusing to remove it.\n' "$DEST"
  exit 1
fi

if [ ! -d "$DEST" ]; then
  printf '  ✓ clinician-first CLI lesson is already absent\n'
  exit 0
fi

if [ ! -f "$DEST/SKILL.md" ] || [ ! -f "$DEST/VERSION" ] \
  || [ ! -f "$DEST/uninstall.sh" ]; then
  printf '  ✗ %s does not look like the installed lesson; refusing to remove it.\n' "$DEST"
  exit 1
fi

rm -rf -- "$DEST"
printf '  ✓ clinician-first CLI lesson removed\n'
printf '    No hooks or Claude settings were changed.\n'
