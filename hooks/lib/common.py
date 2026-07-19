"""Shared helpers for safe-start hooks: stdin parsing, git context, decisions.

Fail-open contract: every hook wraps its work so that an unexpected error
results in the action being ALLOWED. Deliberate prompt-policy rejections are the
one exception: high-confidence credentials and structured patient identifiers
are rejected locally before Claude receives the prompt. Errors are appended to
~/.claude/safe-start/errors.log for later diagnosis.
"""

# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from typing import Optional

# State/log home. SAFE_START_STATE_DIR overrides it so the test suite can run
# without touching the real ~/.claude/safe-start.
LOG_DIR = os.path.expanduser(
  os.environ.get("SAFE_START_STATE_DIR") or "~/.claude/safe-start"
)
PREFIX = "safe-start — "


def _open_private(path: str, append: bool = False):
  """Open a file for writing with owner-only (0600) permissions.

  Everything safe-start writes under LOG_DIR (error logs, per-session state) is
  kept readable only by the owner — it's a privacy tool, so it shouldn't leave
  world-readable files behind. Pair with makedirs(mode=0o700).
  """
  flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
  return os.fdopen(os.open(path, flags, 0o600), "a" if append else "w")


def read_input() -> dict:
  """Parse the hook payload from stdin, returning {} on any error."""
  try:
    return json.load(sys.stdin)
  except Exception:
    return {}


def log_error(where: str, err: BaseException) -> None:
  """Append an error to the safe-start log; never raises."""
  try:
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    with _open_private(os.path.join(LOG_DIR, "errors.log"), append=True) as fh:
      fh.write("[%s] %r\n%s\n" % (where, err, traceback.format_exc()))
  except Exception:
    pass


def git(args: list[str], cwd: str) -> Optional[str]:
  """Run a read-only git command; return stdout, or None on failure."""
  try:
    out = subprocess.run(
      ["git", "-C", cwd, *args],
      capture_output=True, text=True, timeout=3,
    )
    if out.returncode == 0:
      return out.stdout
  except Exception:
    return None
  return None


def project_root(cwd: str) -> str:
  """Return the git repo root of `cwd`, or `cwd` if it isn't a repo."""
  top = git(["rev-parse", "--show-toplevel"], cwd or ".")
  if top and top.strip():
    return top.strip()
  return cwd or os.getcwd()


def ask(reason: str) -> None:
  """Emit a PreToolUse 'ask' decision (warn, then let the user confirm)."""
  print(json.dumps({
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "ask",
      "permissionDecisionReason": reason,
    }
  }))
  sys.exit(0)


def block_prompt(reason: str) -> None:
  """Reject a UserPromptSubmit payload without returning sensitive content.

  UserPromptSubmit uses Claude Code's top-level ``decision`` schema. A block
  prevents the submitted prompt from entering the model context and shows the
  safe, generic reason to the user.
  """
  print(json.dumps({"decision": "block", "reason": reason}))
  sys.exit(0)


def allow() -> None:
  """Allow the action silently."""
  sys.exit(0)


def context(text: str) -> None:
  """Emit non-blocking context (SessionStart / UserPromptSubmit), then exit."""
  if text:
    sys.stdout.write(text)
  sys.exit(0)


def guard(entry, where: str) -> None:
  """Run a hook entrypoint fail-open: any error → allow, and log it."""
  try:
    entry()
  except SystemExit:
    raise
  except BaseException as err:  # noqa: BLE001
    log_error(where, err)
    allow()
