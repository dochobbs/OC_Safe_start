#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""safe-start SessionStart hook: situational awareness, injected as context.

Surfaces only what's relevant this session (otherwise silent). The SKILL tells
Claude how to present these warmly and only when they matter:
  - safety-net (⑪): permissions set to weaken confirm-before-acting prompts
  - cloud-sync: the working folder is inside iCloud/Dropbox/OneDrive/Drive
  - git-state (⑬): detached HEAD / merge / rebase in progress -> offer rescue
  - open-loops (⑨): uncommitted changes, unpushed commits
  - orientation (⑫): project + branch, but only when changed since last session
"""

from __future__ import annotations

import os
import sys

sys.path.insert(
  0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
)

import common  # noqa: E402

STATE = os.path.join(common.LOG_DIR, "last-context")

_CLOUD = [
  ("iCloud", "mobile documents"),
  ("iCloud", "com~apple~clouddocs"),
  ("Dropbox", "/dropbox"),
  ("OneDrive", "/onedrive"),
  ("Google Drive", "/google drive"),
  ("Google Drive", "/googledrive"),
  ("Google Drive", "/my drive"),
]


def _cloud_provider(path: str) -> str | None:
  low = path.lower()
  for name, needle in _CLOUD:
    if needle in low:
      return name
  return None


def _git_state(root: str) -> list[str]:
  notes: list[str] = []
  if common.git(["symbolic-ref", "-q", "HEAD"], root) is None:
    notes.append(
      "you're on a detached HEAD (not on a branch — new commits can get lost)"
    )
  git_dir = common.git(["rev-parse", "--git-dir"], root)
  if git_dir:
    gd = git_dir.strip()
    if not os.path.isabs(gd):
      gd = os.path.join(root, gd)
    if os.path.exists(os.path.join(gd, "MERGE_HEAD")):
      notes.append("a merge is in progress (unfinished)")
    if os.path.exists(os.path.join(gd, "rebase-merge")) or os.path.exists(
      os.path.join(gd, "rebase-apply")
    ):
      notes.append("a rebase is in progress (unfinished)")
  return notes


def _open_loops(root: str) -> list[str]:
  loops: list[str] = []
  st = common.git(["status", "--porcelain"], root)
  if st and st.strip():
    n = len([ln for ln in st.splitlines() if ln.strip()])
    loops.append("%d uncommitted file%s" % (n, "" if n == 1 else "s"))
  ahead = common.git(["rev-list", "--count", "@{u}..HEAD"], root)
  if ahead and ahead.strip().isdigit() and int(ahead.strip()) > 0:
    c = ahead.strip()
    loops.append("%s unpushed commit%s" % (c, "" if c == "1" else "s"))
  return loops


def main() -> None:
  data = common.read_input()
  cwd = data.get("cwd") or os.getcwd()
  mode = data.get("permission_mode", "")
  root = common.project_root(cwd)
  lines: list[str] = []

  if mode in ("bypassPermissions", "acceptEdits", "dontAsk", "auto"):
    lines.append(
      "- Permissions are set to '%s', which weakens one or more "
      "confirm-before-acting prompts — those prompts are read-before-approve "
      "checkpoints. Suggest switching back to the normal ask flow unless "
      "they're sure." % mode
    )

  prov = _cloud_provider(cwd)
  if prov:
    lines.append(
      "- This folder is inside %s. Cloud sync and the agent can edit the same "
      "files at once and collide; a plain local folder (e.g. ~/projects) is "
      "safer. Offer to help move it." % prov
    )

  is_repo = common.git(["rev-parse", "--is-inside-work-tree"], cwd) is not None
  if is_repo:
    for note in _git_state(root):
      lines.append(
        "- Git note: %s. Offer to explain it in plain language and help fix "
        "it — don't let them guess." % note
      )
    loops = _open_loops(root)
    if loops:
      lines.append(
        "- Loose ends: %s. Mention these if they seem to be wrapping up."
        % ", ".join(loops)
      )

  # Orientation — only when the project/branch changed since last session.
  proj = os.path.basename(root)
  branch = None
  if is_repo:
    b = common.git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    branch = b.strip() if b else None
  current = "%s|%s" % (root, branch or "")
  previous = None
  try:
    with open(STATE) as fh:
      previous = fh.read().strip()
  except Exception:
    previous = None
  if current != previous:
    try:
      os.makedirs(common.LOG_DIR, mode=0o700, exist_ok=True)
      with common._open_private(STATE) as fh:
        fh.write(current)
    except Exception:
      pass
    where = "You're working in '%s'" % proj
    if branch:
      where += " on branch '%s'" % branch
    lines.insert(
      0, "- %s. (Quick orientation — say it so they know where they are.)"
      % where
    )

  if lines:
    common.context(
      "[safe-start] Session context — surface these warmly and only as "
      "relevant, in your own words:\n" + "\n".join(lines) + "\n"
    )
  common.allow()


if __name__ == "__main__":
  common.guard(main, "sessionstart")
