"""Shared helpers for safe-start hooks: stdin parsing, git context, decisions.

Fail-open contract: every hook wraps its work so that ANY error results in the
action being ALLOWED. A bug in a safety guard must never block or destroy a
user's work — that would be a worse outcome than the guard being absent. Errors
are appended to ~/.claude/safe-start/errors.log for later diagnosis.
"""

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


def allow() -> None:
  """Allow the action silently."""
  sys.exit(0)


def context(text: str) -> None:
  """Emit non-blocking context (SessionStart / UserPromptSubmit), then exit."""
  if text:
    sys.stdout.write(text)
  sys.exit(0)


def _session_file(session_id: str) -> str:
  sid = session_id or "global"
  safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in sid)[:64]
  return os.path.join(LOG_DIR, "seen-%s.json" % safe)


def seen_this_session(session_id: str, signature: str) -> bool:
  """Record a warning signature; return True if we've already warned it here.

  Lets a hook warn ONCE per session about a given category (e.g. an MRN in a
  prompt) instead of nagging on every repeat — repeated flags on the same
  made-up test data just train alert fatigue, so the real warning stops landing.
  Fails open (returns False → warn).
  """
  path = _session_file(session_id)
  try:
    with open(path) as fh:
      sigs = set(json.load(fh).get("sigs", []))
  except Exception:
    sigs = set()
  if signature in sigs:
    return True
  sigs.add(signature)
  try:
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    with _open_private(path) as fh:
      json.dump({"sigs": sorted(sigs)}, fh)
  except Exception:
    pass
  return False


def guard(entry, where: str) -> None:
  """Run a hook entrypoint fail-open: any error → allow, and log it."""
  try:
    entry()
  except SystemExit:
    raise
  except BaseException as err:  # noqa: BLE001
    log_error(where, err)
    allow()
