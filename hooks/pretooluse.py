#!/usr/bin/env python3
"""safe-start PreToolUse guard.

Fires before Bash / Write / Edit. Never hard-blocks — when it finds something
worth a pause it returns permissionDecision="ask" with a specific, plain-English
reason, which surfaces Claude Code's native confirm dialog. The user always
decides.

Guards here:
  Bash  -> destructive-command (③), secret-in-command (②), commit-hygiene (①/②)
  Write -> scope (⑩), secret-in-content (②)
  Edit  -> scope (⑩), secret-in-new-text (②)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

sys.path.insert(
  0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
)

import common  # noqa: E402
import detectors as d  # noqa: E402

P = common.PREFIX


def _resolve(path: str, cwd: str) -> str:
  path = os.path.expanduser(path)  # a ~ path must not slip past the scope guard
  return path if os.path.isabs(path) else os.path.join(cwd or ".", path)


def _pending_paths(root: str, cmd: str) -> list[str]:
  """Best-effort list of paths a git add/commit is about to record."""
  if "git commit" in cmd:
    out = common.git(["diff", "--cached", "--name-only"], root)
    return [ln.strip() for ln in out.splitlines() if ln.strip()] if out else []
  if "git add" in cmd:
    # Judge only what this add actually touches: a targeted `git add README.md`
    # shouldn't warn about an unrelated .env sitting untracked in the folder.
    # (Anything it misses is still caught at commit time via the staged list.)
    seg = re.split(r"[|;&]", cmd.split("git add", 1)[1])[0]
    targets = [t for t in seg.split() if not t.startswith("-")]
    broad = (not targets or "." in targets
             or re.search(r"(?:^|\s)(?:-A|--all|-u|--update)\b", seg))
    if not broad:
      return targets
    out = common.git(["status", "--porcelain"], root)
    if out:
      # porcelain lines look like "?? path" or " M path"
      return [ln[3:].strip() for ln in out.splitlines() if len(ln) > 3]
  return []


def _conflict_markers_staged(root: str) -> bool:
  """True if any staged file contains an unresolved conflict marker."""
  try:
    out = subprocess.run(
      ["git", "-C", root, "grep", "--cached", "-l", "-E", r"^<{7} "],
      capture_output=True, text=True, timeout=3,
    )
    return out.returncode == 0 and bool(out.stdout.strip())
  except Exception:
    return False


def _check_bash(cmd: str, root: str) -> str | None:
  hit = d.detect_destructive(cmd)
  if hit:
    reason = P + hit.reason
    if hit.offer_snapshot:
      reason += (" If you'd like a Git restore point first, say so — "
                 "otherwise approve to run it as-is.")
    return reason

  secs = d.find_secrets(cmd)
  if secs:
    return P + secs[0].reason

  if "git commit" in cmd or "git add" in cmd:
    for path in _pending_paths(root, cmd):
      hyg = d.check_path_hygiene(path)
      if hyg:
        return P + hyg.reason
    if "git commit" in cmd and _conflict_markers_staged(root):
      return P + ("Some staged files still have unresolved merge-conflict "
                  "markers (<<<<<<<). Commit them anyway?")

  fp = d.command_touches_foreign_path(cmd, root)
  if fp:
    return P + ("`%s` is outside this project — it's in another folder that "
                "might hold patient files or a different project. Go there "
                "anyway?" % fp)
  return None


def _check_write(tool_input: dict, cwd: str, root: str) -> str | None:
  path = (tool_input.get("file_path") or tool_input.get("notebook_path") or "")
  if path and d.is_outside_project(_resolve(path, cwd), root):
    return P + ("That file is outside this project — it's in another folder "
                "that might hold patient files or a different project. "
                "Write there anyway?")
  content = (tool_input.get("content") or tool_input.get("new_string")
             or tool_input.get("new_source") or "")
  secs = d.find_secrets(content)
  if secs:
    return P + secs[0].reason + (" (Better in a .env file, which stays out "
                                 "of Git.)")
  return None


def main() -> None:
  data = common.read_input()
  tool = data.get("tool_name", "")
  tool_input = data.get("tool_input", {}) or {}
  cwd = data.get("cwd") or os.getcwd()
  root = common.project_root(cwd)

  reason = None
  if tool == "Bash":
    reason = _check_bash(tool_input.get("command", "") or "", root)
  elif tool in ("Write", "Edit", "NotebookEdit"):
    reason = _check_write(tool_input, cwd, root)

  if reason:
    common.ask(reason)
  common.allow()


if __name__ == "__main__":
  common.guard(main, "pretooluse")
