#!/usr/bin/env python3
"""safe-start PreToolUse guard.

Fires before shell, file-read, and file-write tools. It returns Claude Code's
permissionDecision="ask" for a specific risk, so the user always decides. Bash
path inspection is best-effort advisory protection, not a sandbox.

Guards here:
  Bash            -> destructive commands, foreign paths, actual Git content
  Read/Glob/Grep   -> project scope and sensitive credential paths
  Write/Edit       -> project/control-plane scope, secret/PHI-in-content
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(
  0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
)

import common  # noqa: E402
import detectors as d  # noqa: E402

P = common.PREFIX


def _resolve(path: str, cwd: str) -> str:
  home = os.path.expanduser("~")
  if path == "$HOME" or path == "${HOME}":
    path = home
  elif path.startswith("$HOME/"):
    path = os.path.join(home, path[len("$HOME/"):])
  elif path.startswith("${HOME}/"):
    path = os.path.join(home, path[len("${HOME}/"):])
  path = os.path.expanduser(path)  # a ~ path must not slip past the scope guard
  return path if os.path.isabs(path) else os.path.join(cwd or ".", path)


def _git_bytes(root: str, args: list[str]) -> bytes | None:
  """Run a read-only Git query and preserve NULs/binary blob bytes."""
  try:
    out = subprocess.run(
      ["git", "-C", root, *args], capture_output=True, timeout=5,
    )
    return out.stdout if out.returncode == 0 else None
  except Exception:
    return None


def _nul_paths(root: str, args: list[str]) -> list[str]:
  out = _git_bytes(root, args)
  if out is None:
    return []
  return [
    item.decode("utf-8", errors="surrogateescape")
    for item in out.split(b"\0") if item
  ]


def _status_entries(
  git_cwd: str, pathspecs: list[str], include_ignored: bool = False
) -> list[tuple[str, str]]:
  args = ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
  if include_ignored:
    args.append("--ignored=matching")
  if pathspecs:
    args.extend(["--", *pathspecs])
  out = _git_bytes(git_cwd, args)
  if out is None:
    return []
  records = out.split(b"\0")
  entries: list[tuple[str, str]] = []
  i = 0
  while i < len(records):
    record = records[i]
    i += 1
    if len(record) < 4:
      continue
    status = record[:2].decode("ascii", errors="replace")
    path = record[3:].decode("utf-8", errors="surrogateescape")
    entries.append((status, path))
    if "R" in status or "C" in status:
      i += 1  # porcelain -z follows a rename/copy with its source path
  return entries


_ADD_VALUE_OPTIONS = {"--chmod", "--pathspec-from-file"}


def _add_candidates(git_cwd: str, args: list[str]) -> list[str]:
  """List worktree files this specific ``git add`` can change in the index."""
  if d._has_short_flag(args, "n") or d._has_long_flag(args, "--dry-run"):
    return []
  targets: list[str] = []
  update_only = d._has_short_flag(args, "u") or d._has_long_flag(
    args, "--update"
  )
  force = d._has_short_flag(args, "f") or d._has_long_flag(args, "--force")
  all_paths = d._has_short_flag(args, "A") or d._has_long_flag(args, "--all")
  after_dashdash = False
  skip_value = False
  pathspec_file = False
  for item in args:
    if skip_value:
      skip_value = False
      continue
    if after_dashdash:
      targets.append(item)
      continue
    if item == "--":
      after_dashdash = True
      continue
    if item in _ADD_VALUE_OPTIONS:
      skip_value = True
      pathspec_file = pathspec_file or item == "--pathspec-from-file"
      continue
    if item.startswith("--pathspec-from-file="):
      pathspec_file = True
      continue
    if item.startswith("-"):
      continue
    targets.append(item)
  if pathspec_file:
    # Avoid pretending we parsed Git's quoting/NUL pathspec-file grammar. A
    # conservative repo-status scan still catches dangerous intended content.
    targets = []
    all_paths = True
  if not targets and not all_paths and not update_only:
    return []  # bare `git add` is an error and changes nothing
  entries = _status_entries(git_cwd, targets, include_ignored=force)
  paths: list[str] = []
  for status, path in entries:
    if status == "!!" and not force:
      continue
    if status == "??" and update_only:
      continue
    paths.append(path)
  if force and not update_only:
    ignored_args = [
      "ls-files", "--others", "--ignored", "--exclude-standard",
      "--full-name", "-z",
    ]
    if targets:
      ignored_args.extend(["--", *targets])
    paths.extend(_nul_paths(git_cwd, ignored_args))
  return list(dict.fromkeys(paths))


def _working_finding(root: str, path: str) -> d.Finding | None:
  full = os.path.join(root, path)
  try:
    if os.path.islink(full):
      content = os.readlink(full).encode("utf-8", errors="surrogateescape")
      return d.check_commit_candidate(path, content)
    if not os.path.isfile(full):
      return None
    size = os.path.getsize(full)
    if size > d._LARGE_FILE_BYTES:
      return d.check_commit_candidate(path, b"", size=size)
    with open(full, "rb") as fh:
      content = fh.read(d._LARGE_FILE_BYTES + 1)
    return d.check_commit_candidate(path, content, size=size)
  except Exception:
    return None


def _staged_finding(root: str, path: str) -> d.Finding | None:
  size_out = _git_bytes(root, ["cat-file", "-s", ":%s" % path])
  if size_out is None:
    return None
  try:
    size = int(size_out.strip())
  except (TypeError, ValueError):
    return None
  if size > d._LARGE_FILE_BYTES:
    return d.check_commit_candidate(path, b"", size=size)
  content = _git_bytes(root, ["cat-file", "blob", ":%s" % path])
  if content is None:
    return None
  return d.check_commit_candidate(path, content, size=size)


def _git_invocations(
  command: str, cwd: str
) -> list[tuple[str, list[str], str, str]]:
  """Parse Git subcommands with preceding ``cd`` and effective ``-C`` cwd."""
  invocations: list[tuple[str, list[str], str, str]] = []
  value_options = {
    "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
  }

  def scan(script: str, starting_cwd: str, depth: int = 0) -> None:
    active_cwd = starting_cwd
    for segment in d._shell_segments(script):
      start = d._command_index(segment)
      if start is None:
        continue
      name = os.path.basename(segment[start])

      if name in ("bash", "sh", "zsh", "dash", "ksh") and depth < 3:
        for position in range(start + 1, len(segment) - 1):
          if segment[position] in ("-c", "--command"):
            scan(segment[position + 1], active_cwd, depth + 1)
            break
        continue

      if name == "cd":
        cd_args = segment[start + 1:]
        if cd_args and cd_args[0] == "--":
          cd_args = cd_args[1:]
        while cd_args and cd_args[0].startswith("-") and cd_args[0] != "-":
          cd_args = cd_args[1:]
        target = cd_args[0] if cd_args else "~"
        if target != "-":
          resolved = _resolve(target, active_cwd)
          if os.path.isdir(resolved):
            active_cwd = os.path.realpath(resolved)
        continue

      if name != "git":
        continue
      git_cwd = active_cwd
      i = start + 1
      while i < len(segment):
        item = segment[i]
        if item == "-C" and i + 1 < len(segment):
          git_cwd = _resolve(segment[i + 1], git_cwd)
          i += 2
          continue
        if item.startswith("-C") and item != "-C":
          git_cwd = _resolve(item[2:], git_cwd)
          i += 1
          continue
        if item in value_options:
          i += 2
          continue
        if any(item.startswith(opt + "=") for opt in value_options
               if opt.startswith("--")):
          i += 1
          continue
        if item.startswith("-"):
          i += 1
          continue
        repo_root = common.project_root(git_cwd)
        invocations.append((item, segment[i + 1:], git_cwd, repo_root))
        break
  scan(command, cwd)
  return invocations


_COMMIT_VALUE_OPTIONS = {
  "-m", "-F", "-C", "-c", "-t", "--message", "--file", "--author",
  "--date", "--cleanup", "--reuse-message", "--reedit-message", "--fixup",
  "--squash", "--template", "--trailer", "--pathspec-from-file",
}


def _commit_candidates(git_cwd: str, args: list[str]) -> tuple[list[str], bool]:
  """Return working-tree paths and whether staged files are also committed."""
  targets: list[str] = []
  include_staged = d._has_long_flag(args, "--include")
  only = d._has_long_flag(args, "--only")
  after_dashdash = False
  skip_value = False
  pathspec_file = False
  for item in args:
    if skip_value:
      skip_value = False
      continue
    if after_dashdash:
      targets.append(item)
      continue
    if item == "--":
      after_dashdash = True
      continue
    if item in _COMMIT_VALUE_OPTIONS:
      skip_value = True
      pathspec_file = pathspec_file or item == "--pathspec-from-file"
      continue
    if item.startswith("--pathspec-from-file="):
      pathspec_file = True
      continue
    if item.startswith("--"):
      continue
    if item.startswith("-"):
      # In a short bundle, m/F/C/c/t consume the next token when last.
      body = item[1:]
      for flag in ("m", "F", "C", "c", "t"):
        pos = body.find(flag)
        if pos >= 0:
          skip_value = pos == len(body) - 1
          break
      continue
    targets.append(item)
  if pathspec_file:
    # Conservatively inspect every tracked changed file rather than attempting
    # Git's quoted/NUL pathspec-file grammar.
    targets = []
    entries = _status_entries(git_cwd, [])
  elif targets:
    entries = _status_entries(git_cwd, targets)
  else:
    return [], not only
  paths = [path for status, path in entries if status not in ("??", "!!")]
  # Explicit paths default to --only; --include additionally commits the
  # already-staged set.
  return paths, include_staged


def _check_git_content(command: str, cwd: str) -> str | None:
  """Inspect exact candidate worktree files and staged blobs before Git writes."""
  staged_cache: dict[str, list[str]] = {}
  future_index: dict[str, set[str]] = {}
  seen: set[tuple[str, str, str]] = set()
  for subcommand, args, git_cwd, repo_root in _git_invocations(command, cwd):
    future = future_index.setdefault(repo_root, set())
    if subcommand == "add":
      candidates = _add_candidates(git_cwd, args)
      interactive = d._has_short_flag(args, "p") or d._has_long_flag(
        args, "--patch"
      )
      for path in candidates:
        key = (repo_root, "worktree", path)
        if key in seen:
          continue
        seen.add(key)
        finding = _working_finding(repo_root, path)
        if finding:
          return P + finding.reason
      if not interactive:
        future.update(candidates)
    elif subcommand == "commit":
      if d._has_long_flag(args, "--dry-run"):
        continue
      commit_all = d._has_short_flag(args, "a") or d._has_long_flag(
        args, "--all"
      )
      explicit_paths, include_staged = _commit_candidates(git_cwd, args)
      working_paths = list(explicit_paths)
      if commit_all:
        working_paths.extend(_nul_paths(
          repo_root, ["diff", "--name-only", "-z", "--diff-filter=ACMR"]
        ))
        include_staged = True
      replacing_index = future.union(working_paths)
      if include_staged:
        staged_paths = staged_cache.get(repo_root)
        if staged_paths is None:
          staged_paths = _nul_paths(
            repo_root,
            ["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
          )
          staged_cache[repo_root] = staged_paths
        for path in staged_paths:
          if path in replacing_index:
            continue
          key = (repo_root, "index", path)
          if key in seen:
            continue
          seen.add(key)
          finding = _staged_finding(repo_root, path)
          if finding:
            return P + finding.reason
      for path in working_paths:
        key = (repo_root, "worktree", path)
        if key in seen:
          continue
        seen.add(key)
        finding = _working_finding(repo_root, path)
        if finding:
          return P + finding.reason
  return None


def _check_bash(cmd: str, root: str, cwd: str) -> str | None:
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

  git_reason = _check_git_content(cmd, cwd)
  if git_reason:
    return git_reason

  fp = d.command_touches_foreign_path(cmd, root, cwd=cwd)
  if fp:
    return P + ("`%s` is outside this project — it's in another folder that "
                "might hold patient files or a different project. Go there "
                "anyway? This Bash path check is advisory, not a sandbox."
                % fp)
  return None


def _check_write(tool_input: dict, cwd: str, root: str) -> str | None:
  path = (tool_input.get("file_path") or tool_input.get("notebook_path") or "")
  resolved = _resolve(path, cwd) if path else ""
  if path and d.is_outside_project(resolved, root):
    return P + ("That file is outside this project — it's in another folder "
                "that might hold patient files or a different project. "
                "Write there anyway?")
  if path and (d.is_agent_control_path(path)
               or d.is_agent_control_path(resolved)):
    return P + (
      "That file controls Claude's permissions or hooks for this project. "
      "Changing it can weaken safe-start itself. Edit it anyway?"
    )
  content = (tool_input.get("content") or tool_input.get("new_string")
             or tool_input.get("new_source") or "")
  secs = d.find_secrets(content)
  if secs:
    extra = "" if secs[0].label == "hard-coded credential" else (
      " Prefer the environment, Keychain, or an approved secret manager. "
      "Use .env only when required and keep it ignored; agents can still "
      "read ignored files."
    )
    return P + secs[0].reason + extra
  phi = d.find_phi_identifiers(content)
  if phi:
    return P + (
      "That edit appears to contain a structured patient identifier (%s). "
      "Keep real patient data out of agent workspaces. Write it anyway?"
      % phi[0].label
    )
  if path and (d.is_sensitive_read_path(path)
               or d.is_sensitive_read_path(resolved)):
    return P + (
      "That path commonly stores credentials or secrets. Changing it can "
      "expose or erase a credential. Edit it anyway?"
    )
  return None


def _read_path_inputs(tool: str, tool_input: dict, cwd: str) -> list[str]:
  """Resolve path-bearing fields for Claude's Read, Glob, and Grep tools."""
  paths: list[str] = []
  for key in ("file_path", "path", "directory", "notebook_path"):
    value = tool_input.get(key)
    if isinstance(value, str) and value:
      paths.append(value)
  extra = tool_input.get("paths")
  if isinstance(extra, list):
    paths.extend(value for value in extra if isinstance(value, str) and value)

  def joined(base: str, pattern: str) -> str:
    if (os.path.isabs(pattern)
        or pattern.startswith(("~", "$HOME", "${HOME}"))):
      return pattern
    return os.path.join(base, pattern)

  if tool == "Glob":
    pattern = tool_input.get("pattern")
    base = tool_input.get("path") or cwd
    if isinstance(pattern, str) and pattern:
      paths.append(joined(base, pattern))
  elif tool == "Grep":
    glob = tool_input.get("glob")
    base = tool_input.get("path") or cwd
    if isinstance(glob, str) and glob:
      paths.append(joined(base, glob))
  return paths or [cwd]


def _check_read(
  tool: str, tool_input: dict, cwd: str, root: str
) -> str | None:
  for raw_path in _read_path_inputs(tool, tool_input, cwd):
    resolved = _resolve(raw_path, cwd)
    if d.is_outside_project(resolved, root, allow_safe_start_read=True):
      return P + (
        "That read is outside this project — it may expose patient files, "
        "credentials, or a different project's data. Read there anyway?"
      )
    if d.is_sensitive_read_path(raw_path) or d.is_sensitive_read_path(resolved):
      return P + (
        "That path commonly contains credentials or secrets. Read it into the "
        "agent context anyway?"
      )
  return None


def main() -> None:
  data = common.read_input()
  tool = data.get("tool_name", "")
  tool_input = data.get("tool_input", {}) or {}
  cwd = data.get("cwd") or os.getcwd()
  root = common.project_root(cwd)

  reason = None
  if tool == "Bash":
    reason = _check_bash(tool_input.get("command", "") or "", root, cwd)
  elif tool in ("Write", "Edit", "NotebookEdit"):
    reason = _check_write(tool_input, cwd, root)
  elif tool in ("Read", "Glob", "Grep"):
    reason = _check_read(tool, tool_input, cwd, root)

  if reason:
    common.ask(reason)
  common.allow()


if __name__ == "__main__":
  common.guard(main, "pretooluse")
