#!/usr/bin/env python3
"""Atomically add, remove, or verify safe-start's Claude Code hooks.

The settings file is validated before it is changed, protected by an advisory
lock, and replaced atomically in its own directory.  Existing settings and
unrelated hooks are preserved.  A successful mutation also leaves a private
``settings.json.safe-start.bak`` containing the immediately previous file.

Usage:
  merge_settings.py add           <hooks_dir>
  merge_settings.py remove        <hooks_dir>
  merge_settings.py verify-absent <hooks_dir>
"""

import contextlib
import copy
import fcntl
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile


CLAUDE_DIR = os.path.expanduser("~/.claude")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
BACKUP = SETTINGS + ".safe-start.bak"
LOCK = os.path.join(CLAUDE_DIR, ".safe-start-settings.lock")
MARKER = "skills/safe-start/hooks/"

# event -> (matcher, script filename)
REGISTRY = {
  "PreToolUse": (
    "Bash|Read|Glob|Grep|Write|Edit|NotebookEdit",
    "pretooluse.py",
  ),
  "UserPromptSubmit": ("", "userpromptsubmit.py"),
  "SessionStart": ("", "sessionstart.py"),
}


class SettingsError(Exception):
  """A validation or transaction failure that must leave settings intact."""


def _display_path(path: str) -> str:
  home = os.path.expanduser("~")
  if path == home:
    return "~"
  if path.startswith(home + os.sep):
    return "~" + path[len(home):]
  return path


def _fail(message: str) -> None:
  raise SettingsError("safe-start: %s" % message)


def _ensure_claude_dir() -> None:
  if os.path.lexists(CLAUDE_DIR) and not os.path.isdir(CLAUDE_DIR):
    _fail("%s is not a directory; I left it untouched." %
          _display_path(CLAUDE_DIR))
  if not os.path.exists(CLAUDE_DIR):
    os.makedirs(CLAUDE_DIR, mode=0o700)
    os.chmod(CLAUDE_DIR, 0o700)


@contextlib.contextmanager
def _settings_lock():
  """Serialize all safe-start settings reads and writes."""
  _ensure_claude_dir()
  if os.path.lexists(LOCK) and os.path.islink(LOCK):
    _fail("%s is a symlink; refusing to use it as a lock." %
          _display_path(LOCK))
  flags = os.O_CREAT | os.O_RDWR
  if hasattr(os, "O_CLOEXEC"):
    flags |= os.O_CLOEXEC
  if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
  try:
    fd = os.open(LOCK, flags, 0o600)
  except OSError as exc:
    _fail("could not open %s safely (%s)." % (_display_path(LOCK), exc))
  info = os.fstat(fd)
  if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
    os.close(fd)
    _fail("%s must be a single regular file; I left it untouched." %
          _display_path(LOCK))
  try:
    os.fchmod(fd, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    yield
  finally:
    try:
      fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
      os.close(fd)


def _read_settings():
  """Return (parsed data, original bytes, existed, original mode)."""
  if not os.path.lexists(SETTINGS):
    return {}, b"", False, 0o600
  if os.path.islink(SETTINGS):
    _fail("%s is a symlink; refusing to replace its target." %
          _display_path(SETTINGS))
  info = os.stat(SETTINGS)
  if not stat.S_ISREG(info.st_mode):
    _fail("%s is not a regular file; I left it untouched." %
          _display_path(SETTINGS))
  with open(SETTINGS, "rb") as fh:
    raw = fh.read()
  if not raw.strip():
    data = {}
  else:
    try:
      data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
      _fail(
        "%s is not valid UTF-8 JSON, so I left it completely untouched. "
        "Fix the JSON and re-run." % _display_path(SETTINGS)
      )
  if not isinstance(data, dict):
    _fail("%s must contain a JSON object; I left it untouched." %
          _display_path(SETTINGS))
  return data, raw, True, stat.S_IMODE(info.st_mode)


def _validate_hooks_schema(data: dict) -> dict:
  """Return hooks after validating every event that we may preserve/edit."""
  if "hooks" not in data:
    return {}
  hooks = data["hooks"]
  if not isinstance(hooks, dict):
    _fail("the hooks section of %s must be an object; I left it untouched." %
          _display_path(SETTINGS))
  for event, groups in hooks.items():
    if not isinstance(event, str) or not isinstance(groups, list):
      _fail("the hooks section of %s has an unexpected shape; I left it "
            "untouched." % _display_path(SETTINGS))
    for group in groups:
      if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        _fail("the %s hook groups in %s have an unexpected shape; I left "
              "them untouched." % (event, _display_path(SETTINGS)))
      for hook in group["hooks"]:
        if not isinstance(hook, dict):
          _fail("the %s hooks in %s have an unexpected shape; I left them "
                "untouched." % (event, _display_path(SETTINGS)))
        if "command" in hook and not isinstance(hook["command"], str):
          _fail("a %s hook command in %s is not text; I left it untouched." %
                (event, _display_path(SETTINGS)))
  return hooks


def _commands(hooks_dir: str) -> dict:
  """Build shell-safe command strings for the destination hook scripts."""
  interpreter = shutil.which("python3")
  if not interpreter:
    _fail("cannot resolve the python3 interpreter used by the hooks.")
  interpreter = os.path.abspath(interpreter)
  return {
    script: "%s %s" % (
      shlex.quote(interpreter),
      shlex.quote(os.path.join(hooks_dir, script)),
    )
    for _, script in REGISTRY.values()
  }


def _is_ours(command: object, expected_commands: set) -> bool:
  return (
    isinstance(command, str)
    and (MARKER in command or command in expected_commands)
  )


def _strip_ours(hooks: dict, expected_commands: set):
  """Remove owned hook commands from every event, preserving all others."""
  cleaned = {}
  removed = 0
  for event, groups in hooks.items():
    new_groups = []
    for group in groups:
      inner = []
      for hook in group["hooks"]:
        if _is_ours(hook.get("command"), expected_commands):
          removed += 1
        else:
          inner.append(copy.deepcopy(hook))
      if inner:
        new_group = copy.deepcopy(group)
        new_group["hooks"] = inner
        new_groups.append(new_group)
    if new_groups:
      cleaned[event] = new_groups
  return cleaned, removed


def _owned_records(hooks: dict, expected_commands: set):
  records = []
  for event, groups in hooks.items():
    for group in groups:
      for hook in group["hooks"]:
        command = hook.get("command")
        if _is_ours(command, expected_commands):
          records.append((event, group.get("matcher"), command))
  return records


def _expected_records(commands: dict):
  return sorted(
    (event, matcher, commands[script])
    for event, (matcher, script) in REGISTRY.items()
  )


def _serialize(data: dict) -> bytes:
  return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _fsync_dir(directory: str) -> None:
  fd = os.open(directory, os.O_RDONLY)
  try:
    os.fsync(fd)
  finally:
    os.close(fd)


def _atomic_write(path: str, content: bytes, mode: int) -> None:
  """Write and replace path atomically using a temporary in the same dir."""
  directory = os.path.dirname(path)
  prefix = ".%s.safe-start." % os.path.basename(path)
  fd, temporary = tempfile.mkstemp(prefix=prefix, dir=directory)
  try:
    os.fchmod(fd, mode)
    with os.fdopen(fd, "wb") as fh:
      fd = -1
      fh.write(content)
      fh.flush()
      os.fsync(fh.fileno())
    os.replace(temporary, path)
    temporary = ""
    _fsync_dir(directory)
  finally:
    if fd >= 0:
      os.close(fd)
    if temporary:
      try:
        os.unlink(temporary)
      except FileNotFoundError:
        pass


def _restore_original(raw: bytes, existed: bool, mode: int) -> None:
  if existed:
    _atomic_write(SETTINGS, raw, mode)
  elif os.path.lexists(SETTINGS):
    os.unlink(SETTINGS)
    _fsync_dir(CLAUDE_DIR)


def _verify(data: dict, mode: str, commands: dict) -> None:
  hooks = _validate_hooks_schema(data)
  expected_commands = set(commands.values())
  records = sorted(_owned_records(hooks, expected_commands))
  if mode == "add":
    if records != _expected_records(commands):
      _fail("hook registration verification failed; restoring prior settings.")
  elif records:
    _fail("owned hooks remain after removal; restoring prior settings.")


def _mutate(mode: str, hooks_dir: str) -> bool:
  commands = _commands(hooks_dir)
  expected_commands = set(commands.values())
  with _settings_lock():
    data, raw, existed, file_mode = _read_settings()
    hooks = _validate_hooks_schema(data)
    cleaned, _ = _strip_ours(hooks, expected_commands)

    if mode == "add":
      for event, (matcher, script) in REGISTRY.items():
        cleaned.setdefault(event, []).append({
          "matcher": matcher,
          "hooks": [{"type": "command", "command": commands[script]}],
        })

    updated = copy.deepcopy(data)
    if cleaned:
      updated["hooks"] = cleaned
    else:
      updated.pop("hooks", None)

    # Convergent updates do not rewrite a file that is already exact, but a
    # successful lifecycle operation still tightens an existing settings file.
    if updated == data:
      _verify(data, mode, commands)
      if existed and file_mode != 0o600:
        os.chmod(SETTINGS, 0o600)
        return True
      return False

    if existed:
      _atomic_write(BACKUP, raw, 0o600)

    try:
      _atomic_write(SETTINGS, _serialize(updated), 0o600)
      written, _, _, _ = _read_settings()
      _verify(written, mode, commands)
    except Exception as exc:
      try:
        _restore_original(raw, existed, file_mode)
      except Exception as rollback_exc:
        _fail("settings update failed (%s), and rollback also failed (%s). "
              "The prior file is available at %s." %
              (exc, rollback_exc, _display_path(BACKUP)))
      if isinstance(exc, SettingsError):
        raise
      _fail("settings update failed; restored the prior file (%s)." % exc)
    return True


def _verify_absent(hooks_dir: str) -> None:
  commands = _commands(hooks_dir)
  with _settings_lock():
    data, _, _, _ = _read_settings()
    _verify(data, "remove", commands)


def _normalize_hooks_dir(path: str) -> str:
  if not path:
    _fail("hooks_dir cannot be empty.")
  return os.path.abspath(os.path.expanduser(path))


def main(argv=None) -> int:
  args = sys.argv[1:] if argv is None else argv
  if len(args) != 2 or args[0] not in {"add", "remove", "verify-absent"}:
    sys.stderr.write(
      "usage: merge_settings.py {add|remove|verify-absent} <hooks_dir>\n"
    )
    return 2

  mode, raw_hooks_dir = args
  try:
    hooks_dir = _normalize_hooks_dir(raw_hooks_dir)
    if mode == "add":
      missing = [
        script for _, script in REGISTRY.values()
        if not os.path.isfile(os.path.join(hooks_dir, script))
      ]
      if missing:
        _fail("refusing to register missing hook scripts: %s" %
              ", ".join(sorted(missing)))
      changed = _mutate(mode, hooks_dir)
      print("settings %s (%s)" %
            ("updated" if changed else "already current", mode))
    elif mode == "remove":
      changed = _mutate(mode, hooks_dir)
      print("settings %s (%s)" %
            ("updated" if changed else "already current", mode))
    else:
      _verify_absent(hooks_dir)
      print("safe-start hooks absent")
    return 0
  except (OSError, SettingsError) as exc:
    sys.stderr.write("%s\n" % exc)
    return 3


if __name__ == "__main__":
  sys.exit(main())
