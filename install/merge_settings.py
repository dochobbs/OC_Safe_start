#!/usr/bin/env python3
"""Idempotently add or remove safe-start's hooks in ~/.claude/settings.json.

Preserves every other setting and every OTHER hook the user already has. Our
hooks are identified by the marker "skills/safe-start/hooks/" in their command,
so add is idempotent (strip-then-add) and remove is surgical.

Usage:
  merge_settings.py add    <hooks_dir>
  merge_settings.py remove <hooks_dir>
"""

import json
import os
import sys

SETTINGS = os.path.expanduser("~/.claude/settings.json")
MARKER = "skills/safe-start/hooks/"

# event -> (matcher, script filename)
REGISTRY = {
  "PreToolUse": ("Bash|Write|Edit|NotebookEdit", "pretooluse.py"),
  "UserPromptSubmit": ("", "userpromptsubmit.py"),
  "SessionStart": ("", "sessionstart.py"),
}


def _load() -> dict:
  """Load settings, but NEVER silently discard a real-but-broken file.

  If the file doesn't exist (or is empty), start fresh. If it exists but won't
  parse, abort loudly and leave it untouched — overwriting it would erase the
  user's model, env, permissions, and their own hooks.
  """
  if not os.path.exists(SETTINGS):
    return {}
  with open(SETTINGS) as fh:
    raw = fh.read()
  if not raw.strip():
    return {}
  try:
    return json.loads(raw)
  except Exception:
    sys.stderr.write(
      "safe-start: ~/.claude/settings.json isn't valid JSON, so I left it "
      "completely untouched (nothing was lost). Fix the JSON — usually a stray "
      "comma — and re-run the installer.\n"
    )
    sys.exit(3)


def _save(data: dict) -> None:
  os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
  with open(SETTINGS, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")


def _strip_ours(groups: list) -> list:
  """Drop safe-start's hooks from an event's matcher-groups, keep the rest.

  Leaves any unfamiliar shape (a non-dict group, a non-list hooks value)
  untouched rather than crashing on a hand-edited settings file.
  """
  out = []
  for grp in groups:
    if not isinstance(grp, dict) or not isinstance(grp.get("hooks"), list):
      out.append(grp)
      continue
    inner = [
      h for h in grp["hooks"]
      if not (isinstance(h, dict) and MARKER in h.get("command", ""))
    ]
    if inner:
      new = dict(grp)
      new["hooks"] = inner
      out.append(new)
  return out


def main(mode: str, hooks_dir: str) -> None:
  data = _load()
  hooks = data.get("hooks", {}) or {}
  if not isinstance(hooks, dict) or any(
    event in hooks and not isinstance(hooks[event], list) for event in REGISTRY
  ):
    sys.stderr.write(
      "safe-start: the hooks section of ~/.claude/settings.json has an "
      "unexpected shape, so I left it untouched. Fix it and re-run.\n"
    )
    sys.exit(3)

  # Always strip ours first (makes add idempotent and remove clean).
  for event in REGISTRY:
    if event in hooks:
      hooks[event] = _strip_ours(hooks[event])

  if mode == "add":
    for event, (matcher, script) in REGISTRY.items():
      command = 'python3 "%s/%s"' % (hooks_dir.rstrip("/"), script)
      entry = {"matcher": matcher,
               "hooks": [{"type": "command", "command": command}]}
      hooks.setdefault(event, []).append(entry)

  # Drop now-empty event arrays.
  hooks = {k: v for k, v in hooks.items() if v}
  if hooks:
    data["hooks"] = hooks
  elif "hooks" in data:
    del data["hooks"]

  _save(data)
  print("settings updated (%s)" % mode)


if __name__ == "__main__":
  the_mode = sys.argv[1] if len(sys.argv) > 1 else "add"
  the_dir = (
    sys.argv[2] if len(sys.argv) > 2
    else os.path.expanduser("~/.claude/skills/safe-start/hooks")
  )
  main(the_mode, the_dir)
