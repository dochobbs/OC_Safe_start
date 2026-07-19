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

from __future__ import annotations

import os
import re
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


def find_secrets(text: str) -> list[Finding]:
  """Find likely secrets/credentials in a blob of text.

  Args:
    text: Arbitrary text (a prompt, a command, or file content).

  Returns:
    A de-duplicated list of Findings with kind == "secret".
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
    m = _ASSIGNED_SECRET.search(text)
    if m:
      # Skip obvious placeholders so we don't nag on templates.
      value = m.group(2).lower()
      placeholders = {"your_key_here", "changeme", "xxxxxxxxxxxxxxxx", "example"}
      if value not in placeholders and "xxxx" not in value:
        findings.append(
          Finding(
            "secret",
            "hard-coded credential",
            "That looks like a hard-coded key or password. Keep secrets out of "
            "code, prompts, and commits — use a .env file (which safe-start "
            "keeps out of Git).",
          )
        )
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


def find_phi_identifiers(text: str) -> list[Finding]:
  """Find structured identifiers that may indicate PHI.

  This is a best-effort backstop. It reliably catches structured identifiers
  (SSN, MRN, DOB, phone, email) but CANNOT catch free-text clinical narrative
  with a real name and history. The trained habit ("no real patient data") is
  the real defense.

  Args:
    text: Arbitrary text (a prompt, a command, or file content).

  Returns:
    A de-duplicated list of Findings with kind == "phi".
  """
  findings: list[Finding] = []
  seen: set[str] = set()
  for label, pattern, reason in _PHI_PATTERNS:
    if re.search(pattern, text) and label not in seen:
      seen.add(label)
      findings.append(Finding("phi", label, reason))
  return findings


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


# Each entry: (label, compiled regex, reason, offer_snapshot)
_DESTRUCTIVE: list[tuple[str, re.Pattern[str], str, bool]] = [
  (
    "git reset --hard",
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    "This throws away every uncommitted change with no undo.",
    True,
  ),
  (
    "git checkout -- .",
    re.compile(r"\bgit\s+checkout\s+(?:--\s+)?\.(?:\s|$)"),
    "This discards all your uncommitted edits and there's no undo.",
    True,
  ),
  (
    "git clean -fd",
    re.compile(r"\bgit\s+clean\b[^\n|;&]*-\w*f"),
    "This permanently deletes untracked files — anything not yet committed is "
    "gone for good.",
    True,
  ),
  (
    "find -delete",
    re.compile(r"\bfind\b[^\n|;&]*\s-delete\b"),
    "This finds and deletes many files at once — anything not committed is gone.",
    True,
  ),
  (
    "git branch -D",
    re.compile(r"\bgit\s+branch\s+-D\b"),
    "Force-deleting a branch throws away any commits on it that aren't saved "
    "somewhere else.",
    False,
  ),
  (
    "git stash drop",
    re.compile(r"\bgit\s+stash\s+(?:drop|clear)\b"),
    "This permanently deletes stashed changes — a dropped stash has no undo.",
    False,
  ),
  (
    "git push --force",
    re.compile(r"\bgit\s+push\b[^\n|;&]*(?:--force\b|--force-with-lease\b|\s-f\b)"),
    "A force-push can overwrite history on the shared copy — teammates can lose "
    "work.",
    False,
  ),
]


_RM_REASON = (
  "This deletes a whole folder and its contents, and Git can't bring back "
  "anything that wasn't committed."
)

_GIT_RESTORE = re.compile(r"\bgit\s+restore\b([^\n|;&]*)")


def _git_restore_discards(command: str) -> bool:
  """Return True if a `git restore` in the command would erase worktree edits.

  Plain `git restore <path>` (and `--worktree` / `--source` forms) overwrite
  uncommitted edits exactly like `git checkout -- .`. Only `--staged` alone is
  safe — it unstages without touching the worktree copy.
  """
  m = _GIT_RESTORE.search(command)
  if not m:
    return False
  args = m.group(1)
  staged = re.search(r"(?:^|\s)(?:--staged|-S)\b", args)
  worktree = re.search(r"(?:^|\s)(?:--worktree|-W)\b", args)
  return not staged or bool(worktree)


def _rm_is_recursive_force(command: str) -> bool:
  """Return True if the command runs `rm` with BOTH recursive and force flags.

  A plain `rm file.txt` (recoverable if tracked) is intentionally not flagged;
  only recursive + force deletion is. Flags are parsed rather than pattern-
  matched, so `rm -rf`, `rm -fr`, `rm -r -f`, and `rm -Rf` all count while
  `rm -f file` and `rm -r dir` (each alone) do not.

  Args:
    command: The shell command string.

  Returns:
    True if any `rm` invocation carries both -r and -f (in any spelling).
  """
  tokens = (
    command.replace(";", " ; ").replace("&&", " && ").replace("|", " | ").split()
  )
  for i, tok in enumerate(tokens):
    if tok == "rm" or tok.endswith("/rm"):
      flags = ""
      for nxt in tokens[i + 1:]:
        if nxt == "--" or nxt.startswith("--"):
          continue
        if nxt.startswith("-"):
          flags += nxt[1:]
        else:
          break
      low = flags.lower()
      if "r" in low and "f" in low:
        return True
  return False


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
  if _rm_is_recursive_force(command):
    return DestructiveHit("rm -rf", _RM_REASON, True)
  if _git_restore_discards(command):
    return DestructiveHit(
      "git restore",
      "This overwrites your uncommitted edits with the last saved version — "
      "changes that were never committed can't be brought back.",
      True,
    )
  for label, pattern, reason, offer_snapshot in _DESTRUCTIVE:
    if pattern.search(command):
      return DestructiveHit(label, reason, offer_snapshot)
  return None


# --------------------------------------------------------------------------- #
# Scope — keep the agent inside the project.
# --------------------------------------------------------------------------- #

def _real(path: str) -> str:
  return os.path.realpath(os.path.expanduser(path))


def is_outside_project(path: str, project_root: str) -> bool:
  """Return True if `path` is outside the project root.

  An allowlist covers legitimate out-of-project locations (the user's
  ~/.claude config). Everything else outside the project root — a sibling
  project, the home directory, system paths — counts as outside.

  Args:
    path: The file path a tool is about to read or write.
    project_root: The project's root directory (repo root or launch dir).

  Returns:
    True if the path is outside the project and not allowlisted.
  """
  rp = _real(path)
  root = _real(project_root)
  allow = [_real("~/.claude")]
  if rp == root or rp.startswith(root + os.sep):
    return False
  for a in allow:
    if rp == a or rp.startswith(a + os.sep):
      return False
  return True


def command_touches_foreign_path(command: str, project_root: str) -> Optional[str]:
  """Return a path in a shell command that points into the user's OTHER files.

  Write/Edit scope-guarding (is_outside_project) misses the case where the agent
  reads or copies via Bash — e.g. `cat ~/Documents/patients.csv`. This scans a
  command for absolute paths under the user's home directory that fall outside
  the project (and outside ~/.claude). System paths (/usr, /tmp, ...) and
  in-project paths are ignored. Best-effort and advisory — it warns, never blocks.

  Args:
    command: The shell command string.
    project_root: The project's root directory.

  Returns:
    The first offending path token, or None.
  """
  home = _real("~")
  root = _real(project_root)
  claude = _real("~/.claude")
  # Path preceded by start/space/quote/=/( — so quoting doesn't hide it — while a
  # URL's "//" (preceded by ":") and ssh's "user/repo" (preceded by a word char)
  # are not matched.
  for tok in re.findall(r"""(?:^|[\s'"=(])(~?/[^\s'")]+)""", command):
    rp = _real(tok)
    if not rp.startswith(home + os.sep):
      continue  # not user data (system path, /tmp, a URL, ...)
    if rp == root or rp.startswith(root + os.sep):
      continue  # inside the project
    if rp == claude or rp.startswith(claude + os.sep):
      continue  # our own config
    return tok
  return None


# --------------------------------------------------------------------------- #
# Commit hygiene — things that shouldn't be committed.
# --------------------------------------------------------------------------- #

_JUNK_PATH_PATTERNS = [
  re.compile(r"(?:^|/)node_modules(?:/|$)"),
  re.compile(r"(?:^|/)\.env(?:\.|$)"),
  re.compile(r"(?:^|/)\.DS_Store$"),
  re.compile(r"(?:^|/)(?:dist|build|__pycache__|\.venv|venv)(?:/|$)"),
  re.compile(r"\.(?:key|pem|p12|pfx)$"),
  re.compile(r"(?:^|/)private(?:/|$)"),
]

_LARGE_FILE_BYTES = 10 * 1024 * 1024  # 10 MB


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


def has_conflict_markers(text: str) -> bool:
  """Return True if text contains unresolved merge-conflict markers.

  Args:
    text: File content.

  Returns:
    True if a `<<<<<<<` conflict marker is present at line start.
  """
  return re.search(r"(?m)^<{7}\s", text) is not None
