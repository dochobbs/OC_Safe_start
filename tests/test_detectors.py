"""Tests for the safe-start detection engine.

Runnable two ways:
  python3 tests/test_detectors.py      # standalone, no dependencies
  pytest tests/test_detectors.py       # if pytest is available

Each test function asserts; the standalone runner at the bottom collects and
reports pass/fail so the installer can self-verify on any machine.
"""

import os
import sys

sys.path.insert(
  0, os.path.join(os.path.dirname(__file__), "..", "hooks", "lib")
)

import detectors as d  # noqa: E402


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #

def test_secret_aws_key():
  assert d.find_secrets("key=AKIAIOSFODNN7EXAMPLE now")
  assert d.find_secrets("AKIAIOSFODNN7EXAMPLE")[0].label == "AWS access key"


def test_secret_openai_and_anthropic():
  assert any(f.label == "OpenAI API key"
             for f in d.find_secrets("sk-abcdefghijklmnopqrstuvwx1234"))
  assert any(f.label == "Anthropic API key"
             for f in d.find_secrets("sk-ant-api03-abcdefghijklmnopqrst-xyz"))


def test_secret_github_google_slack_jwt():
  assert d.find_secrets("ghp_" + "a" * 36)
  assert d.find_secrets("AIza" + "b" * 35)
  # Fixture split so GitHub push protection doesn't read it as a live token;
  # the detector still sees the joined string.
  assert d.find_secrets("xoxb-" + "123456789012-abcdefghijklmnop")
  jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEFghiJKL"
  assert d.find_secrets(jwt)


def test_secret_private_key_block():
  assert d.find_secrets("-----BEGIN OPENSSH PRIVATE KEY-----")
  assert d.find_secrets("-----BEGIN PRIVATE KEY-----")


def test_secret_assigned_credential():
  assert d.find_secrets('API_KEY = "abcd1234efgh5678ijkl"')
  assert d.find_secrets("password: hunter2hunter2hunter2")


def test_secret_placeholder_is_ignored():
  assert not d.find_secrets("API_KEY=your_key_here")
  assert not d.find_secrets("token = xxxxxxxxxxxxxxxx")


def test_secret_clean_text_negative():
  assert not d.find_secrets("Please summarize this well-child note for me.")
  assert not d.find_secrets("git commit -m 'fix the button'")


# --------------------------------------------------------------------------- #
# PHI identifiers
# --------------------------------------------------------------------------- #

def test_phi_ssn():
  assert any(f.label == "SSN" for f in d.find_phi_identifiers("SSN 123-45-6789"))


def test_phi_mrn_dob_phone_email():
  assert any(f.label == "MRN"
             for f in d.find_phi_identifiers("MRN: 0048213"))
  assert any(f.label == "date of birth"
             for f in d.find_phi_identifiers("DOB: 03/14/2019"))
  assert any(f.label == "phone number"
             for f in d.find_phi_identifiers("call 415-555-0132"))
  assert any(f.label == "email address"
             for f in d.find_phi_identifiers("parent: jane.doe@example.com"))


def test_phi_clean_text_negative():
  # Free-text narrative with no structured identifiers is (honestly) not caught.
  assert not d.find_phi_identifiers(
    "4 year old with three days of fever, otherwise well."
  )


# --------------------------------------------------------------------------- #
# Destructive commands
# --------------------------------------------------------------------------- #

def test_destructive_rm_variants():
  for cmd in ["rm -rf build", "rm -fr build", "rm -r -f build",
              "rm -Rf ./dist", "rm -f -r old", "sudo rm -rf /tmp/x"]:
    hit = d.detect_destructive(cmd)
    assert hit is not None and hit.label == "rm -rf", cmd
    assert hit.offer_snapshot is True


def test_destructive_rm_safe_negatives():
  for cmd in ["rm file.txt", "rm -f note.md", "rm -r emptydir",
              "rmdir foo", "trash old.txt"]:
    assert d.detect_destructive(cmd) is None, cmd


def test_destructive_git_erasers():
  assert d.detect_destructive("git reset --hard HEAD~1").label == "git reset --hard"
  assert d.detect_destructive("git checkout -- .").label == "git checkout -- ."
  assert d.detect_destructive("git checkout .").label == "git checkout -- ."
  assert d.detect_destructive("git clean -fd").label == "git clean -fd"
  assert d.detect_destructive("git clean -xdf").label == "git clean -fd"


def test_destructive_force_push():
  assert d.detect_destructive("git push --force origin main").label == "git push --force"
  assert d.detect_destructive("git push -f").label == "git push --force"
  assert d.detect_destructive(
    "git push --force-with-lease").label == "git push --force"


def test_destructive_git_safe_negatives():
  for cmd in ["git commit -m 'x'", "git checkout main", "git push",
              "git clean -n", "git reset HEAD file.txt", "git status"]:
    assert d.detect_destructive(cmd) is None, cmd


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #

def test_scope_inside_and_outside():
  root = "/tmp/proj"
  assert d.is_outside_project("/tmp/proj/src/app.py", root) is False
  assert d.is_outside_project("/tmp/proj", root) is False
  assert d.is_outside_project("/tmp/other-proj/secret.py", root) is True
  assert d.is_outside_project(os.path.expanduser("~/Documents/notes.txt"),
                              root) is True


def test_scope_claude_config_allowlisted():
  root = "/tmp/proj"
  assert d.is_outside_project(
    os.path.expanduser("~/.claude/settings.json"), root) is False


# --------------------------------------------------------------------------- #
# Commit hygiene
# --------------------------------------------------------------------------- #

def test_hygiene_flags_junk():
  for p in ["node_modules/left-pad/index.js", ".env", "src/.env.local",
            ".DS_Store", "keys/server.key", "private/patients.csv"]:
    assert d.check_path_hygiene(p) is not None, p


def test_hygiene_allows_normal_files():
  for p in ["src/app.py", "README.md", "index.html", "styles/main.css"]:
    assert d.check_path_hygiene(p) is None, p


def test_conflict_markers():
  assert d.has_conflict_markers("line\n<<<<<<< HEAD\nx\n=======\ny\n") is True
  assert d.has_conflict_markers("no markers here <<<<<<< inline") is False


# --------------------------------------------------------------------------- #
# Red-team fixes: more destructive foot-guns + scope-via-Bash
# --------------------------------------------------------------------------- #

def test_destructive_more_footguns():
  assert d.detect_destructive("find . -name '*.tmp' -delete").label == "find -delete"
  assert d.detect_destructive("git branch -D feature").label == "git branch -D"
  assert d.detect_destructive("git stash drop").label == "git stash drop"
  assert d.detect_destructive("git stash clear").label == "git stash drop"


def test_destructive_more_negatives():
  for cmd in ["git branch feature", "git branch", "git stash", "git stash list",
              "find . -name '*.py'", "find . -type f"]:
    assert d.detect_destructive(cmd) is None, cmd


def test_scope_bash_foreign_path():
  import tempfile
  import shutil
  home = os.path.expanduser("~")
  root = tempfile.mkdtemp(dir=home)  # a project directory inside home
  try:
    foreign = os.path.join(home, "Documents", "patients.csv")
    assert d.command_touches_foreign_path("cat %s" % foreign, root) is not None
    assert d.command_touches_foreign_path("cat %s/notes.txt" % root, root) is None
    assert d.command_touches_foreign_path("cat /usr/bin/env", root) is None
    assert d.command_touches_foreign_path(
      "cat ~/.claude/settings.json", root) is None
    assert d.command_touches_foreign_path(
      "curl https://example.com/x", root) is None
    # quoting must not hide the path (red-team round 2)
    assert d.command_touches_foreign_path("cat '%s'" % foreign, root) is not None
    assert d.command_touches_foreign_path('cat "%s"' % foreign, root) is not None
  finally:
    shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Review fixes (2026-07-18): env-var secret names, sk-proj keys, git restore
# --------------------------------------------------------------------------- #

def test_secret_env_var_style_names():
  # Underscore-prefixed names are how secrets actually get pasted (.env style);
  # a leading \b can't see past the underscore, so these all used to slip by.
  for text in ["OPENAI_API_KEY=abcdefghij1234567890",
               "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCY",
               "GITHUB_TOKEN=abcdefghij1234567890",
               "my_password=abcdefghij1234567890",
               "STRIPE_SECRET_KEY=abcdefghij1234567890"]:
    assert d.find_secrets(text), text
  # Words that merely contain a secret-ish substring must stay quiet.
  for text in ["tokenizer=abcdefghij1234567890",
               "monkey=abcdefghij1234567890",
               "keyboard_layout=abcdefghij1234567890"]:
    assert not d.find_secrets(text), text


def test_secret_openai_project_key():
  # Modern OpenAI keys are sk-proj-… (hyphenated), not legacy sk-….
  assert any(f.label == "OpenAI API key" for f in d.find_secrets(
    "sk-proj-Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56"))
  # sk-ant must still resolve to Anthropic, never OpenAI.
  found = d.find_secrets("sk-ant-api03-abcdefghijklmnopqrst-xyz")
  assert any(f.label == "Anthropic API key" for f in found)
  assert not any(f.label == "OpenAI API key" for f in found)


def test_destructive_git_restore():
  # `git restore` erases uncommitted worktree edits exactly like
  # `git checkout -- .` — and it's the spelling a modern agent picks.
  for cmd in ["git restore .", "git restore src/app.py",
              "git restore --worktree app.py",
              "git restore --staged --worktree app.py",
              "git restore --source=HEAD~2 app.py"]:
    hit = d.detect_destructive(cmd)
    assert hit is not None and hit.label == "git restore", cmd
  # --staged alone only unstages; the worktree copy is untouched.
  assert d.detect_destructive("git restore --staged app.py") is None


# --------------------------------------------------------------------------- #
# Standalone runner
# --------------------------------------------------------------------------- #

def _run() -> int:
  tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
  passed = 0
  failed = 0
  for t in tests:
    try:
      t()
      passed += 1
    except AssertionError as e:
      failed += 1
      print("FAIL: %s  %s" % (t.__name__, e))
    except Exception as e:  # noqa: BLE001
      failed += 1
      print("ERROR: %s  %r" % (t.__name__, e))
  print("\n%d passed, %d failed, %d total" % (passed, failed, passed + failed))
  return 1 if failed else 0


if __name__ == "__main__":
  sys.exit(_run())
