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
  key = "AKIA" + "IOSFODNN7EXAMPLE"
  assert d.find_secrets("key=%s now" % key)
  assert d.find_secrets(key)[0].label == "AWS access key"


def test_secret_openai_and_anthropic():
  assert any(f.label == "OpenAI API key"
             for f in d.find_secrets("sk-" + "abcdefghijklmnopqrstuvwx1234"))
  assert any(f.label == "Anthropic API key"
             for f in d.find_secrets(
               "sk-ant-" + "api03-abcdefghijklmnopqrst-xyz"))


def test_secret_github_google_slack_jwt():
  assert d.find_secrets("ghp_" + "a" * 36)
  assert d.find_secrets("AIza" + "b" * 35)
  # Fixture split so GitHub push protection doesn't read it as a live token;
  # the detector still sees the joined string.
  assert d.find_secrets("xoxb-" + "123456789012-abcdefghijklmnop")
  jwt = ("eyJhbGciOiJIUzI1NiJ9" + "." +
         "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "abcDEFghiJKL")
  assert d.find_secrets(jwt)


def test_secret_private_key_block():
  marker = "PRIVATE KEY-----"
  assert d.find_secrets("-----BEGIN OPENSSH " + marker)
  assert d.find_secrets("-----BEGIN " + marker)


def test_secret_assigned_credential():
  found = d.find_secrets('API_KEY = "abcd1234efgh5678ijkl"')
  assert found
  assert "approved secret manager" in found[0].reason
  assert "agents can still read ignored files" in found[0].reason
  assert d.find_secrets("password: hunter2hunter2hunter2")


def test_secret_placeholder_is_ignored():
  assert not d.find_secrets("API_KEY=your_key_here")
  assert not d.find_secrets("token = xxxxxxxxxxxxxxxx")
  assert d.find_secrets(
    "API_KEY=your_key_here\nPASSWORD=" + "z" * 24
  ), "a placeholder must not hide a later credential"


def test_secret_clean_text_negative():
  assert not d.find_secrets("Please summarize this well-child note for me.")
  assert not d.find_secrets("git commit -m 'fix the button'")


# --------------------------------------------------------------------------- #
# PHI identifiers
# --------------------------------------------------------------------------- #

def test_phi_ssn():
  assert any(f.label == "SSN" for f in d.find_phi_identifiers("SSN 321-54-6789"))


def test_phi_mrn_dob_phone_email():
  assert any(f.label == "MRN"
             for f in d.find_phi_identifiers("MRN: 0048213"))
  assert any(f.label == "date of birth"
             for f in d.find_phi_identifiers("DOB: 03/14/2019"))
  assert any(f.label == "phone number"
             for f in d.find_phi_identifiers("patient callback: 415-867-5309"))
  assert any(f.label == "email address"
             for f in d.find_phi_identifiers("parent: jane.doe@hospital.org"))


def test_phi_obvious_synthetic_placeholders_are_ignored():
  for text in [
    "SSN 123-45-6789",
    "MRN: 123456",
    "call 415-555-0132",
    "parent: jane.doe@example.com",
    "parent: jane.doe@example.org",
    "parent: jane.doe@example.net",
    "parent: fake@clinic.test",
  ]:
    assert not d.find_phi_identifiers(text), text
  found = d.find_phi_identifiers(
    "docs: jane@example.com; parent contact: jane@hospital.org"
  )
  assert any(f.label == "email address" for f in found)


def test_phi_clean_text_negative():
  # Free-text narrative with no structured identifiers is (honestly) not caught.
  assert not d.find_phi_identifiers(
    "4 year old with three days of fever, otherwise well."
  )
  assert not d.find_phi_identifiers(
    "Please change our support email to hello@offcall.com."
  )
  assert not d.find_phi_identifiers(
    "Update the website footer to 312-867-5309 for the office."
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
  assert d.detect_destructive("git checkout -- .") is not None
  assert d.detect_destructive("git checkout .") is not None
  assert d.detect_destructive("git clean -fd").label == "git clean -fd"
  assert d.detect_destructive("git clean -xdf").label == "git clean -fd"


def test_destructive_git_checkout_switch_worktree_and_force_refspec():
  for cmd in [
    "git checkout -- src/app.py",
    "git checkout -f main",
    "git switch --discard-changes main",
    "git switch -f main",
    "git worktree remove --force ../scratch",
    "git push origin +main",
  ]:
    assert d.detect_destructive(cmd) is not None, cmd
  for cmd in [
    "git checkout main",
    "git checkout --",
    "git switch main",
    "git worktree remove ../clean-tree",
  ]:
    assert d.detect_destructive(cmd) is None, cmd


def test_destructive_force_push():
  assert d.detect_destructive("git push --force origin main").label == "git push --force"
  assert d.detect_destructive("git push -f").label == "git push --force"
  assert d.detect_destructive(
    "git push --force-with-lease").label == "git push --force"


def test_destructive_git_safe_negatives():
  for cmd in ["git commit -m 'x'", "git checkout main", "git push",
              "git clean -n", "git clean -nfd", "git clean --dry-run --force",
              "git reset HEAD file.txt", "git status",
              "echo git reset --hard", "printf 'rm -rf build'",
              "echo rm -rf build"]:
    assert d.detect_destructive(cmd) is None, cmd


def test_destructive_common_command_wrappers_still_detect():
  for cmd in [
    "sudo rm -rf build",
    "env SAFE=1 git reset --hard",
    "command git clean -fd",
    "nice -n 5 git reset --hard",
  ]:
    assert d.detect_destructive(cmd) is not None, cmd


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


def test_scope_claude_control_plane_is_not_write_allowlisted():
  root = "/tmp/proj"
  assert d.is_outside_project(
    os.path.expanduser("~/.claude/settings.json"), root) is True


def test_scope_only_safe_start_lesson_templates_can_be_read():
  root = "/tmp/proj"
  assert d.is_outside_project(
    os.path.expanduser("~/.claude/skills/safe-start/SKILL.md"), root,
    allow_safe_start_read=True,
  ) is False
  assert d.is_outside_project(
    os.path.expanduser("~/.claude/skills/safe-start/templates/CLAUDE.md"), root,
    allow_safe_start_read=True,
  ) is False
  assert d.is_outside_project(
    os.path.expanduser("~/.claude/safe-start/config.json"), root,
    allow_safe_start_read=True,
  ) is False
  assert d.is_outside_project(
    os.path.expanduser("~/.claude/settings.json"), root,
    allow_safe_start_read=True,
  ) is True


# --------------------------------------------------------------------------- #
# Commit hygiene
# --------------------------------------------------------------------------- #

def test_hygiene_flags_junk():
  for p in ["node_modules/left-pad/index.js", ".env", "src/.env.local",
            ".envrc", ".netrc", ".npmrc", ".pypirc", ".ssh/id_ed25519",
            ".DS_Store", "keys/server.key", "private/patients.csv"]:
    assert d.check_path_hygiene(p) is not None, p


def test_hygiene_allows_normal_files():
  for p in ["src/app.py", "README.md", "index.html", "styles/main.css",
            ".env.example", ".env.sample", ".env.template"]:
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
  home = os.path.expanduser("~")
  root = os.path.join(home, "safe-start-project")
  foreign = os.path.join(home, "Documents", "patients.csv")
  assert d.command_touches_foreign_path("cat %s" % foreign, root) is not None
  assert d.command_touches_foreign_path("cat %s/notes.txt" % root, root) is None
  assert d.command_touches_foreign_path("cat /usr/bin/env", root) is None
  assert d.command_touches_foreign_path("cat /tmp/example", root) is None
  assert d.command_touches_foreign_path(
    "cat ~/.claude/settings.json", root) is not None
  assert d.command_touches_foreign_path(
    "curl https://example.com/x", root) is None
  # Quoting and redirection must not hide the path.
  assert d.command_touches_foreign_path("cat '%s'" % foreign, root) is not None
  assert d.command_touches_foreign_path('cat "%s"' % foreign, root) is not None
  assert d.command_touches_foreign_path("cat x >$HOME/out.txt", root) is not None
  assert d.command_touches_foreign_path("cat x 2>${HOME}/out.txt", root) is not None
  assert d.command_touches_foreign_path("cat x>../out.txt", root) is not None
  assert d.command_touches_foreign_path("ls /Volumes/Clinical", root) is not None


def test_scope_bash_parent_path_can_stay_inside_from_subdirectory():
  root = "/tmp/project"
  cwd = "/tmp/project/src"
  assert d.command_touches_foreign_path("cat ../README.md", root, cwd) is None
  assert d.command_touches_foreign_path("cat ../../outside", root, cwd) is not None


# --------------------------------------------------------------------------- #
# Review fixes (2026-07-18): env-var secret names, sk-proj keys, git restore
# --------------------------------------------------------------------------- #

def test_secret_env_var_style_names():
  # Underscore-prefixed names are how secrets actually get pasted (.env style);
  # a leading \b can't see past the underscore, so these all used to slip by.
  for text in ["OPENAI_API_" + "KEY=abcdefghij1234567890",
               "AWS_SECRET_ACCESS_" + "KEY=wJalrXUtnFEMIK7MDENGbPxRfiCY",
               "GITHUB_" + "TOKEN=abcdefghij1234567890",
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
    "sk-" + "proj-Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56"))
  # sk-ant must still resolve to Anthropic, never OpenAI.
  found = d.find_secrets("sk-ant-" + "api03-abcdefghijklmnopqrst-xyz")
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


def test_destructive_global_options_long_options_and_nested_shell():
  cases = {
    "git -C . reset --hard HEAD": "git reset --hard",
    "git --no-pager -C . clean --directories --force": "git clean -fd",
    "git -C . branch --delete --force old": "git branch -D",
    "git -C . push --force-with-lease=main origin main": "git push --force",
    "rm --recursive --force build": "rm -rf",
    "bash -c 'rm --recursive --force build'": "rm -rf",
  }
  for command, label in cases.items():
    hit = d.detect_destructive(command)
    assert hit is not None and hit.label == label, command


def test_sensitive_read_paths_and_patterns():
  for path in [
    ".env", ".env.local", ".envrc", "config/secrets.yaml", "credentials.json",
    "keys/server.pem", "~/.ssh/id_ed25519", ".env*", "**/secrets/*.json",
  ]:
    assert d.is_sensitive_read_path(path), path
  for path in ["README.md", "src/config.py", "certificates/README.txt",
               "~/.ssh/id_ed25519.pub", ".env.example", ".env.sample",
               ".env.template"]:
    assert not d.is_sensitive_read_path(path), path
  assert d.is_agent_control_path(".claude/settings.json")
  assert d.is_agent_control_path("project/.claude/settings.local.json")
  assert not d.is_agent_control_path("src/settings.json")


def test_commit_candidate_scans_content_type_and_size():
  secret = ("token=" + "a" * 32).encode("ascii")
  finding = d.check_commit_candidate("config.txt", secret)
  assert finding is not None and finding.kind == "secret"

  finding = d.check_commit_candidate("notes.txt", b"MRN: 8097342\n")
  assert finding is not None and finding.kind == "phi"

  finding = d.check_commit_candidate("scan.dat", b"binary\x00payload")
  assert finding is not None and finding.label == "binary file"

  finding = d.check_commit_candidate("report.pdf", b"%PDF-1.7\nASCII body\n")
  assert finding is not None and finding.label == "binary file"

  finding = d.check_commit_candidate("legacy.dat", b"text\xffmore")
  assert finding is not None and finding.label == "binary file"

  finding = d.check_commit_candidate("movie.dat", b"", 10 * 1024 * 1024 + 1)
  assert finding is not None and finding.label == "large file"

  assert d.check_commit_candidate("src/app.py", b"print('safe')\n") is None


def test_hygiene_flags_likely_phi_filenames():
  for path in ["patients.csv", "exports/phi-data.json", "mrn_list.txt",
               "chart-export.xlsx", "medical_records.ndjson"]:
    assert d.check_path_hygiene(path) is not None, path


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
