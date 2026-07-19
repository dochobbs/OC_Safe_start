"""Detection engine for safe-start guards.

Pure, side-effect-free functions used by the safe-start Claude Code hooks.
Each detector returns structured findings that a hook turns into a plain-English
warning. Nothing here mutates state or performs I/O — that keeps it fully
unit-testable and keeps the hooks fail-open (a detector bug can never destroy a
user's work).

Design notes:
- Secret detection is high-confidence (known key shapes).
- PHI-identifier detection is best-effort and explicitly acknowledged as such;
  free-text clinical narrative is NOT reliably detectable (see the safe-start
  design spec, section 6). The habit is the real defense; this is a backstop.
- Destructive-command detection is scoped to the irreversible work/history
  erasers only, so it never fires during normal editing.
"""

# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import re
import shlex
from typing import NamedTuple, Optional


class Finding(NamedTuple):
  """A single detector hit.

  Attributes:
    kind: Machine-readable category, e.g. "secret" or "phi".
    label: Short human label, e.g. "AWS secret key".
    reason: One-sentence, plain-English, specific warning for the user.
  """

  kind: str
  label: str
  reason: str


# --------------------------------------------------------------------------- #
# Secrets — high confidence. Order matters: most specific first.
# --------------------------------------------------------------------------- #

_SECRET_PATTERNS: list[tuple[str, str, str]] = [
  (
    "private key",
    r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----",
    "That's a private key. If it lands in a commit it's in your Git history "
    "permanently, even after you delete the file.",
  ),
  (
    "AWS access key",
    r"\bAKIA[0-9A-Z]{16}\b",
    "That looks like an AWS access key. Committing it can let scraper bots "
    "compromise the account within minutes of a push.",
  ),
  (
    "Anthropic API key",
    r"\bsk-ant-[A-Za-z0-9_\-]{20,}",
    "That's an Anthropic API key. Keep it out of prompts, files, and commits.",
  ),
  (
    "OpenAI API key",
    # Both the modern prefixed form (sk-proj-…, hyphenated) and legacy sk-….
    r"\bsk-(?!ant-)(?:(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}"
    r"|[A-Za-z0-9]{20,})",
    "That looks like an OpenAI API key. Keep it out of prompts, files, and "
    "commits.",
  ),
  (
    "GitHub token",
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{22,}",
    "That's a GitHub token. Anyone who sees it can act as you on GitHub.",
  ),
  (
    "Google API key",
    r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "That looks like a Google API key. Keep it out of prompts and commits.",
  ),
  (
    "Slack token",
    r"\bxox[baprs]-[0-9A-Za-z\-]{10,}",
    "That's a Slack token. Keep it out of prompts and commits.",
  ),
  (
    "JSON Web Token",
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b",
    "That looks like a JWT (a signed token). Treat it like a password.",
  ),
]

# Medium-confidence: a secret-ish name assigned a long opaque value.
# The lookbehind (not \b) lets underscore-prefixed env-var names match —
# OPENAI_API_KEY=, AWS_SECRET_ACCESS_KEY=, GITHUB_TOKEN= are how secrets are
# actually pasted. The trailing \b keeps "tokenizer"/"keyboard" quiet.
_ASSIGNED_SECRET = re.compile(
  r"(?i)(?<![a-z0-9])(api[_-]?key|key|secret|token|passwd|password|"
  r"client[_-]?secret|access[_-]?token|private[_-]?key)\b"
  r"\s*[:=]\s*['\"]?([A-Za-z0-9/+_\-]{16,})",
)


def find_secrets(
  text: str, high_confidence_only: bool = False
) -> list[Finding]:
  """Find likely secrets/credentials in a blob of text.

  Args:
    text: Arbitrary text (a prompt, a command, or file content).

  Returns:
    A de-duplicated list of Findings with kind == "secret". When
    ``high_confidence_only`` is true, generic assigned values must be longer
    and contain both letters and digits; provider-specific shapes are always
    retained.
  """
  findings: list[Finding] = []
  seen: set[str] = set()
  for label, pattern, reason in _SECRET_PATTERNS:
    if re.search(pattern, text):
      if label not in seen:
        seen.add(label)
        findings.append(Finding("secret", label, reason))
  # Only run the noisy generic matcher if nothing specific already matched,
  # to avoid double-flagging the same value.
  if not findings:
    for m in _ASSIGNED_SECRET.finditer(text):
      # Skip obvious placeholders so we don't nag on templates.
      value = m.group(2).lower()
      placeholders = {"your_key_here", "changeme", "xxxxxxxxxxxxxxxx", "example"}
      if value in placeholders or "xxxx" in value:
        continue
      if high_confidence_only and not (
        len(value) >= 20
        and re.search(r"[a-z]", value, re.IGNORECASE)
        and re.search(r"\d", value)
      ):
        continue
      findings.append(
        Finding(
          "secret",
          "hard-coded credential",
          "That looks like a hard-coded key or password. Keep secrets out of "
          "code, prompts, and commits — use the environment, Keychain, or an "
          "approved secret manager. Use .env only when the project requires "
          "it, and keep it ignored; agents can still read ignored files.",
        )
      )
      break
  return findings


# --------------------------------------------------------------------------- #
# PHI identifiers — best-effort backstop, NOT a guarantee.
# --------------------------------------------------------------------------- #

_PHI_PATTERNS: list[tuple[str, str, str]] = [
  (
    "SSN",
    r"\b\d{3}-\d{2}-\d{4}\b",
    "That looks like a Social Security number.",
  ),
  (
    "MRN",
    r"(?i)\bMRN[:#]?\s*\d{5,}\b",
    "That looks like a medical record number.",
  ),
  (
    "date of birth",
    r"(?i)\b(?:dob|d\.o\.b\.?|date of birth)\b[:\s]*"
    r"(?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12]\d|3[01])[/\-](?:19|20)\d\d",
    "That looks like a date of birth.",
  ),
  (
    "email address",
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    "That looks like an email address.",
  ),
  (
    "phone number",
    r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b",
    "That looks like a phone number.",
  ),
]

_CONTACT_CLINICAL_CONTEXT = re.compile(
  r"(?i)\b(?:patient|pt\.?|parent|guardian|caregiver|mother|father|child)\b"
)
_CONTACT_BUSINESS_CONTEXT = re.compile(
  r"(?i)(?:\b(?:support|helpdesk|sales|office|company|team|business|"
  r"organization|billing department)\s+(?:email|phone|number|contact)\b|"
  r"\b(?:website|footer|contact us)\b)"
)


def _contact_has_clinical_context(text: str, match: re.Match) -> bool:
  """Require nearby person-of-care context before classifying contact data.

  An email address or phone number alone is ordinary product/business content,
  not evidence of PHI. Keep the hard prompt block for contacts explicitly tied
  to a patient, parent, guardian, or caregiver, while letting common support and
  website details through.
  """
  window = text[max(0, match.start() - 96):match.end() + 96]
  if _CONTACT_BUSINESS_CONTEXT.search(window):
    return False
  return _CONTACT_CLINICAL_CONTEXT.search(window) is not None


def find_phi_identifiers(text: str) -> list[Finding]:
  """Find structured identifiers that may indicate PHI.

  This is a best-effort backstop. It catches SSNs, MRNs, DOBs, and contact
  details tied to nearby patient/parent context, but CANNOT catch free-text
  clinical narrative with a real name and history. The trained habit ("no real
  patient data") is the real defense.

  Args:
    text: Arbitrary text (a prompt, a command, or file content).

  Returns:
    A de-duplicated list of Findings with kind == "phi".
  """
  findings: list[Finding] = []
  seen: set[str] = set()
  for label, pattern, reason in _PHI_PATTERNS:
    match = next(
      (candidate for candidate in re.finditer(pattern, text)
       if not _looks_synthetic_phi(label, candidate.group(0))
       and (label not in ("email address", "phone number")
            or _contact_has_clinical_context(text, candidate))),
      None,
    )
    if match is not None and label not in seen:
      seen.add(label)
      findings.append(Finding("phi", label, reason))
  return findings


def _looks_synthetic_phi(label: str, value: str) -> bool:
  """Return True for a small set of unmistakable documentation fixtures.

  The prompt guard should not punish someone for following examples in a
  tutorial. Keep this allowlist narrow: ambiguity still blocks.
  """
  low = value.lower()
  if label == "email address":
    domain = low.rsplit("@", 1)[-1].rstrip(".")
    return (
      domain in ("example.com", "example.net", "example.org")
      or domain.endswith((".example.com", ".example.net", ".example.org"))
      or domain.endswith((".example", ".invalid", ".test"))
    )
  digits = re.sub(r"\D", "", value)
  if label == "phone number":
    if len(digits) == 11 and digits.startswith("1"):
      digits = digits[1:]
    # NANP reserves 555-0100 through 555-0199 for fictional use.
    return (
      len(digits) == 10
      and digits[3:6] == "555"
      and 100 <= int(digits[6:]) <= 199
    )
  if label == "SSN" and len(digits) == 9:
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    return (
      digits == "123456789"
      or area in ("000", "666")
      or area.startswith("9")
      or group == "00"
      or serial == "0000"
    )
  if label == "MRN":
    mrn = re.sub(r"\D", "", value)
    return bool(mrn) and (
      len(set(mrn)) == 1
      or mrn in ("12345", "123456", "1234567", "0123456")
    )
  return False


# --------------------------------------------------------------------------- #
# Destructive commands — the irreversible work/history erasers only.
# --------------------------------------------------------------------------- #

class DestructiveHit(NamedTuple):
  """A destructive-command match.

  Attributes:
    label: Short label, e.g. "rm -rf".
    reason: Plain-English explanation of the irreversible risk.
    offer_snapshot: Whether it's worth offering a Git checkpoint first.
  """

  label: str
  reason: str
  offer_snapshot: bool


_RM_REASON = (
  "This deletes a whole folder and its contents, and Git can't bring back "
  "anything that wasn't committed."
)


def _shell_segments(command: str) -> list[list[str]]:
  """Tokenize top-level shell commands without executing or expanding them."""
  try:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
  except (TypeError, ValueError):
    tokens = command.replace(";", " ; ").split()
  segments: list[list[str]] = []
  current: list[str] = []
  for token in tokens:
    if token and all(c in ";&|" for c in token):
      if current:
        segments.append(current)
        current = []
    else:
      current.append(token)
  if current:
    segments.append(current)
  return segments


def _all_shell_segments(command: str, depth: int = 0) -> list[list[str]]:
  """Include scripts passed to common ``shell -c`` wrappers."""
  segments = _shell_segments(command)
  if depth >= 3:
    return segments
  nested: list[list[str]] = []
  shells = {"bash", "sh", "zsh", "dash", "ksh"}
  for segment in segments:
    for i, token in enumerate(segment):
      if os.path.basename(token) not in shells:
        continue
      for j in range(i + 1, len(segment) - 1):
        if segment[j] in ("-c", "--command"):
          nested.extend(_all_shell_segments(segment[j + 1], depth + 1))
          break
  return segments + nested


_SHELL_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _command_index(tokens: list[str]) -> Optional[int]:
  """Return the actual command token after common shell wrappers.

  This is intentionally small, but it distinguishes executable positions from
  harmless arguments such as ``echo git reset --hard``. It also preserves the
  common ``sudo``, ``env``, ``command``, ``time``, and ``nice`` forms agents use.
  """
  i = 0
  while i < len(tokens) and _SHELL_ASSIGNMENT.match(tokens[i]):
    i += 1
  while i < len(tokens):
    name = os.path.basename(tokens[i])
    if name in ("command", "builtin", "exec", "nohup", "time"):
      i += 1
      while i < len(tokens) and tokens[i].startswith("-"):
        i += 1
      continue
    if name == "nice":
      i += 1
      if i < len(tokens) and tokens[i] in ("-n", "--adjustment"):
        i += 2
      elif i < len(tokens) and tokens[i].startswith("--adjustment="):
        i += 1
      continue
    if name == "env":
      i += 1
      while i < len(tokens):
        item = tokens[i]
        if item == "--":
          i += 1
          break
        if item in ("-u", "--unset", "-C", "--chdir"):
          i += 2
          continue
        if item.startswith(("--unset=", "--chdir=")):
          i += 1
          continue
        if item.startswith("-") or _SHELL_ASSIGNMENT.match(item):
          i += 1
          continue
        break
      continue
    if name == "sudo":
      i += 1
      value_options = {
        "-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
        "-C", "--close-from", "-T", "--command-timeout", "-D", "--chdir",
        "-R", "--chroot", "-t", "--type",
      }
      while i < len(tokens):
        item = tokens[i]
        if item == "--":
          i += 1
          break
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
        break
      continue
    break
  return i if i < len(tokens) else None


_GIT_GLOBAL_VALUE_OPTIONS = {
  "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
}


def _git_invocations(tokens: list[str]) -> list[tuple[str, list[str]]]:
  """Return (subcommand, args) pairs, skipping Git's global options."""
  start = _command_index(tokens)
  if start is None or os.path.basename(tokens[start]) != "git":
    return []
  i = start + 1
  while i < len(tokens):
    item = tokens[i]
    if item == "--":
      i += 1
      break
    if item in _GIT_GLOBAL_VALUE_OPTIONS:
      i += 2
      continue
    if item.startswith("-C") and item != "-C":
      i += 1
      continue
    if item.startswith("-c") and item != "-c":
      i += 1
      continue
    if any(item.startswith(opt + "=") for opt in _GIT_GLOBAL_VALUE_OPTIONS
           if opt.startswith("--")):
      i += 1
      continue
    if item.startswith("-"):
      i += 1
      continue
    return [(item, tokens[i + 1:])]
  return []


def _has_short_flag(args: list[str], flag: str) -> bool:
  """Find a one-letter option in short-option bundles (e.g. -nfd)."""
  option_args = args[:args.index("--")] if "--" in args else args
  return any(
    item.startswith("-")
    and not item.startswith("--")
    and flag in item[1:]
    for item in option_args
  )


def _has_long_flag(args: list[str], name: str) -> bool:
  option_args = args[:args.index("--")] if "--" in args else args
  return any(
    item == name or item.startswith(name + "=") for item in option_args
  )


def _rm_is_recursive_force(tokens: list[str]) -> bool:
  """Return True for short or long recursive+force options on any ``rm``."""
  i = _command_index(tokens)
  if i is None or os.path.basename(tokens[i]) != "rm":
    return False
  args = tokens[i + 1:]
  recursive = _has_short_flag(args, "r") or _has_short_flag(args, "R")
  recursive = recursive or _has_long_flag(args, "--recursive")
  force = _has_short_flag(args, "f") or _has_long_flag(args, "--force")
  return recursive and force


def detect_destructive(command: str) -> Optional[DestructiveHit]:
  """Detect an irreversible/high-blast-radius shell command.

  Only the work/history erasers are flagged (rm -rf, git reset --hard,
  git checkout -- ., git clean -fd, git push --force). Ordinary commands —
  including a plain `rm file.txt` of a tracked file — are intentionally NOT
  flagged, so this never fires during normal work.

  Args:
    command: The shell command string from a Bash tool call.

  Returns:
    A DestructiveHit for the first match, or None.
  """
  for tokens in _all_shell_segments(command):
    if _rm_is_recursive_force(tokens):
      return DestructiveHit("rm -rf", _RM_REASON, True)
    i = _command_index(tokens)
    if (i is not None and os.path.basename(tokens[i]) == "find"
        and "-delete" in tokens[i + 1:]):
      return DestructiveHit(
        "find -delete",
        "This finds and deletes many files at once — anything not committed "
        "is gone.",
        True,
      )
    for subcommand, args in _git_invocations(tokens):
      if subcommand == "reset" and _has_long_flag(args, "--hard"):
        return DestructiveHit(
          "git reset --hard",
          "This throws away every uncommitted change with no undo.",
          True,
        )
      if subcommand == "checkout":
        forced = _has_short_flag(args, "f") or _has_long_flag(args, "--force")
        dashdash = args.index("--") if "--" in args else -1
        replaces_paths = dashdash >= 0 and bool(args[dashdash + 1:])
        paths = [item for item in args if not item.startswith("-")]
        if forced or replaces_paths or "." in paths:
          return DestructiveHit(
            "git checkout (discard changes)",
            "This can overwrite uncommitted file edits, and there's no undo "
            "for changes Git never saved.",
            True,
          )
      if subcommand == "switch" and (
        _has_short_flag(args, "f")
        or _has_long_flag(args, "--force")
        or _has_long_flag(args, "--discard-changes")
      ):
        return DestructiveHit(
          "git switch --discard-changes",
          "This can discard uncommitted file edits while switching branches.",
          True,
        )
      if subcommand == "restore":
        staged = _has_short_flag(args, "S") or _has_long_flag(args, "--staged")
        worktree = _has_short_flag(args, "W") or _has_long_flag(
          args, "--worktree"
        )
        if not staged or worktree:
          return DestructiveHit(
            "git restore",
            "This overwrites your uncommitted edits with the last saved "
            "version — changes that were never committed can't be brought back.",
            True,
          )
      if subcommand == "clean":
        dry_run = _has_short_flag(args, "n") or _has_long_flag(args, "--dry-run")
        force = _has_short_flag(args, "f") or _has_long_flag(args, "--force")
        if force and not dry_run:
          return DestructiveHit(
            "git clean -fd",
            "This permanently deletes untracked files — anything not yet "
            "committed is gone for good.",
            True,
          )
      if subcommand == "branch":
        forced_delete = _has_short_flag(args, "D") or (
          (_has_short_flag(args, "d") or _has_long_flag(args, "--delete"))
          and (_has_short_flag(args, "f") or _has_long_flag(args, "--force"))
        )
        if forced_delete:
          return DestructiveHit(
            "git branch -D",
            "Force-deleting a branch throws away any commits on it that aren't "
            "saved somewhere else.",
            False,
          )
      if subcommand == "stash" and args and args[0] in ("drop", "clear"):
        return DestructiveHit(
          "git stash drop",
          "This permanently deletes stashed changes — a dropped stash has no "
          "undo.",
          False,
        )
      if subcommand == "push":
        forced = _has_short_flag(args, "f") or _has_long_flag(args, "--force")
        forced = forced or _has_long_flag(args, "--force-with-lease")
        forced = forced or any(
          item.startswith("+") and len(item) > 1 for item in args
        )
        if forced:
          return DestructiveHit(
            "git push --force",
            "A force-push can overwrite history on the shared copy — teammates "
            "can lose work.",
            False,
          )
      if subcommand == "worktree" and args and args[0] == "remove" and (
        _has_short_flag(args[1:], "f")
        or _has_long_flag(args[1:], "--force")
      ):
        return DestructiveHit(
          "git worktree remove --force",
          "Force-removing a worktree can delete uncommitted changes inside it.",
          True,
        )
  return None


# --------------------------------------------------------------------------- #
# Scope — keep the agent inside the project.
# --------------------------------------------------------------------------- #

def _real(path: str) -> str:
  return os.path.realpath(os.path.expanduser(path))


def _is_safe_start_read_path(path: str) -> bool:
  """Allow only the installed lesson/templates and exact owned config file."""
  installed = _real("~/.claude/skills/safe-start")
  lesson = os.path.join(installed, "SKILL.md")
  templates = os.path.join(installed, "templates")
  config = _real("~/.claude/safe-start/config.json")
  return (
    path == lesson
    or path == config
    or path == templates
    or path.startswith(templates + os.sep)
  )


def is_outside_project(
  path: str, project_root: str, allow_safe_start_read: bool = False
) -> bool:
  """Return True if `path` is outside the project root.

  There is deliberately no general ~/.claude allowlist: writes there can alter
  the agent's own control plane. Read tools may opt into the narrow installed
  safe-start lesson/template allowlist.

  Args:
    path: The file path a tool is about to read or write.
    project_root: The project's root directory (repo root or launch dir).

  Returns:
    True if the path is outside the project and not allowlisted.
  """
  rp = _real(path)
  root = _real(project_root)
  if rp == root or rp.startswith(root + os.sep):
    return False
  if allow_safe_start_read and _is_safe_start_read_path(rp):
    return False
  return True


_FOREIGN_PATH = re.compile(
  r"(?<![A-Za-z0-9_:])("  # avoid URL components and ordinary words
  r"(?:~|\$HOME|\$\{HOME\})(?:/[^\s'\";|&<>()]*)?"
  r"|/(?:[^\s'\";|&<>()]+)"
  r"|(?:\.\./)+(?:[^\s'\";|&<>()]*)?"
  r")"
)


def _resolve_shell_path(path: str, cwd: str) -> str:
  if path == "$HOME" or path == "${HOME}":
    path = os.path.expanduser("~")
  elif path.startswith("$HOME/"):
    path = os.path.join(os.path.expanduser("~"), path[len("$HOME/"):])
  elif path.startswith("${HOME}/"):
    path = os.path.join(os.path.expanduser("~"), path[len("${HOME}/"):])
  else:
    path = os.path.expanduser(path)
  return path if os.path.isabs(path) else os.path.join(cwd, path)


def command_touches_foreign_path(
  command: str, project_root: str, cwd: Optional[str] = None
) -> Optional[str]:
  """Return a path in a shell command that points into the user's OTHER files.

  Write/Edit scope-guarding (is_outside_project) misses the case where the agent
  reads or copies via Bash — e.g. `cat ~/Documents/patients.csv`. This scans a
  command for home paths, /Users, /Volumes, and relative ``../`` escapes that
  resolve outside the project. It sees quoted and unspaced-redirection forms,
  and scans nested shell text because it works on the raw command. System and
  temporary paths remain quiet. This is explicitly best-effort and advisory;
  shell syntax is too dynamic for this to be a security boundary.

  Args:
    command: The shell command string.
    project_root: The project's root directory.

  Returns:
    The first offending path token, or None.
  """
  root = _real(project_root)
  launch = _real(cwd or project_root)
  home = _real("~")
  for match in _FOREIGN_PATH.finditer(command):
    tok = match.group(1).rstrip(",:")
    rp = _real(_resolve_shell_path(tok, launch))
    if rp == root or rp.startswith(root + os.sep):
      continue  # inside the project
    expanded_home = rp == home or rp.startswith(home + os.sep)
    explicit_user_volume = rp.startswith(("/Users/", "/Volumes/"))
    relative_escape = tok.startswith("../")
    home_expression = tok.startswith(("~", "$HOME", "${HOME}"))
    if expanded_home or explicit_user_volume or relative_escape or home_expression:
      return tok
  return None


# --------------------------------------------------------------------------- #
# Commit hygiene — things that shouldn't be committed.
# --------------------------------------------------------------------------- #

_JUNK_PATH_PATTERNS = [
  re.compile(r"(?:^|/)node_modules(?:/|$)"),
  re.compile(
    r"(?:^|/)\.env(?!(?:\.example|\.sample|\.template)(?:/|$))[^/]*(?:/|$)",
    re.IGNORECASE,
  ),
  re.compile(r"(?:^|/)(?:\.netrc|\.npmrc|\.pypirc)$", re.IGNORECASE),
  re.compile(r"(?:^|/)(?:id_rsa|id_ecdsa|id_ed25519)$"),
  re.compile(r"(?:^|/)\.DS_Store$"),
  re.compile(r"(?:^|/)(?:dist|build|__pycache__|\.venv|venv)(?:/|$)"),
  re.compile(r"\.(?:key|pem|p12|pfx)$", re.IGNORECASE),
  re.compile(
    r"(?:^|/)(?:credentials?|secrets?)(?:[/._ -]|$)", re.IGNORECASE
  ),
  re.compile(r"(?:^|/)private(?:/|$)"),
  re.compile(
    r"(?:^|/)(?:patients?|phi|mrn|medical[-_ ]?records?|chart[-_ ]?exports?)"
    r"(?:[/._ -]|$)",
    re.IGNORECASE,
  ),
]

_LARGE_FILE_BYTES = 10 * 1024 * 1024  # 10 MB

_SENSITIVE_READ_PATTERNS = [
  re.compile(
    r"(?:^|/)\.env(?!(?:\.example|\.sample|\.template)(?:/|$))[^/]*(?:/|$)",
    re.IGNORECASE,
  ),
  re.compile(r"\.(?:key|pem|p12|pfx)(?:$|[.*?\]}])", re.IGNORECASE),
  re.compile(
    r"(?:^|/)(?:credentials?|secrets?)(?:[/._*?{}\[\]-]|$)",
    re.IGNORECASE,
  ),
  re.compile(r"(?:^|/)(?:id_rsa|id_ecdsa|id_ed25519)$"),
  re.compile(r"(?:^|/)(?:\.netrc|\.npmrc|\.pypirc)$", re.IGNORECASE),
  re.compile(
    r"(?:^|/)\.claude/settings[^/]*\.json$", re.IGNORECASE
  ),
]


def is_sensitive_read_path(path: str) -> bool:
  """Return True when a read path/pattern commonly holds credentials."""
  normalized = path.replace(os.sep, "/")
  return any(pattern.search(normalized) for pattern in _SENSITIVE_READ_PATTERNS)


def is_agent_control_path(path: str) -> bool:
  """Return True for project-local Claude settings that change guard behavior."""
  normalized = path.replace(os.sep, "/")
  return re.search(
    r"(?:^|/)\.claude/settings[^/]*\.json$",
    normalized,
    re.IGNORECASE,
  ) is not None


def check_path_hygiene(path: str) -> Optional[Finding]:
  """Flag a path that probably shouldn't be committed.

  Args:
    path: A repo-relative or absolute path being staged/committed.

  Returns:
    A Finding with kind == "hygiene", or None.
  """
  for pat in _JUNK_PATH_PATTERNS:
    if pat.search(path):
      return Finding(
        "hygiene",
        "shouldn't be committed",
        "`%s` looks like something Git shouldn't track (a secret, a big "
        "generated folder, or private data). Commit anyway?" % path,
      )
  return None


def check_commit_candidate(
  path: str, content: bytes, size: Optional[int] = None
) -> Optional[Finding]:
  """Inspect the exact bytes Git is about to stage or commit.

  Callers supply either worktree bytes or the staged blob. The detector is
  deliberately side-effect free so tests can distinguish those two surfaces.
  Findings never include a matched credential or patient identifier.
  """
  byte_count = len(content) if size is None else size
  if byte_count > _LARGE_FILE_BYTES:
    return Finding(
      "hygiene",
      "large file",
      "`%s` is larger than 10 MiB. Large files make Git history permanently "
      "heavier; commit it anyway?" % path,
    )
  hygiene = check_path_hygiene(path)
  if hygiene:
    return hygiene
  sample = content[:8192]
  binary_magic = (
    b"%PDF-", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a",
    b"PK\x03\x04", b"\x1f\x8b", b"\xca\xfe\xba\xbe",
  )
  try:
    text = content.decode("utf-8")
  except UnicodeDecodeError:
    text = ""
  if (b"\x00" in sample or any(sample.startswith(magic) for magic in binary_magic)
      or (content and not text)):
    return Finding(
      "hygiene",
      "binary file",
      "`%s` appears to be binary. Binary files are hard to review and can "
      "bloat Git history; commit it anyway?" % path,
    )
  secrets = find_secrets(text)
  if secrets:
    return Finding(
      "secret",
      secrets[0].label,
      "`%s` appears to contain a credential. Remove it and rotate it if it "
      "may be real before putting it in Git." % path,
    )
  phi = find_phi_identifiers(text)
  if phi:
    return Finding(
      "phi",
      phi[0].label,
      "`%s` appears to contain a structured patient identifier (%s). Keep "
      "real patient data out of Git." % (path, phi[0].label),
    )
  if has_conflict_markers(text):
    return Finding(
      "hygiene",
      "merge-conflict markers",
      "`%s` still contains unresolved merge-conflict markers (<<<<<<<). "
      "Commit it anyway?" % path,
    )
  return None


def has_conflict_markers(text: str) -> bool:
  """Return True if text contains unresolved merge-conflict markers.

  Args:
    text: File content.

  Returns:
    True if a `<<<<<<<` conflict marker is present at line start.
  """
  return re.search(r"(?m)^<{7}\s", text) is not None
