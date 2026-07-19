"""Integration tests for the safe-start hooks: realistic payloads in, output out.

The detectors can be perfect while a hook reads the wrong payload field and
silently never fires. Each test pipes a realistic Claude Code hook payload into
the script and asserts on its decision, so schema drift fails loudly instead
of silently in production.

All state is redirected into a temp dir via SAFE_START_STATE_DIR, so running
these never touches ~/.claude/safe-start.

Runnable two ways:
  python3 tests/test_hooks.py      # standalone, no dependencies
  pytest tests/test_hooks.py       # if pytest is available
"""

import json
import os
import subprocess
import sys
import tempfile

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")


def _run(script: str, payload: dict, state_dir: str) -> str:
  """Pipe a hook payload into a hook script; return its stdout."""
  env = dict(os.environ, SAFE_START_STATE_DIR=state_dir)
  out = subprocess.run(
    [sys.executable, os.path.join(HOOKS, script)],
    input=json.dumps(payload), capture_output=True, text=True,
    timeout=15, env=env,
  )
  assert out.returncode == 0, out.stderr
  return out.stdout


def _init_repo(path: str) -> None:
  subprocess.run(["git", "init", "-q", path], check=True)
  subprocess.run(
    ["git", "-C", path, "config", "user.email", "safe-start@example.invalid"],
    check=True,
  )
  subprocess.run(
    ["git", "-C", path, "config", "user.name", "safe-start tests"],
    check=True,
  )


# --------------------------------------------------------------------------- #
# UserPromptSubmit — the prompt PHI/secret scan
# --------------------------------------------------------------------------- #

def test_userpromptsubmit_blocks_phi_in_canonical_prompt():
  # Current Claude Code sends the typed message as "prompt".
  with tempfile.TemporaryDirectory() as state:
    out = _run("userpromptsubmit.py",
               {"prompt": "pt MRN: 8675309, needs follow-up",
                "session_id": "t-phi", "cwd": "/tmp"}, state)
    decision = json.loads(out)
    assert decision["decision"] == "block", out
    assert "structured patient identifier" in decision["reason"], out
    assert "8675309" not in out, "block reason must not echo the identifier"


def test_userpromptsubmit_blocks_secret_without_echoing_it():
  with tempfile.TemporaryDirectory() as state:
    key = "AKIA" + "IOSFODNN7EXAMPLE"
    out = _run("userpromptsubmit.py",
               {"prompt": "use %s for the bucket" % key,
                "session_id": "t-sec", "cwd": "/tmp"}, state)
    decision = json.loads(out)
    assert decision["decision"] == "block", out
    assert "secret or credential" in decision["reason"], out
    assert key not in out, "block reason must not echo the credential"


def test_userpromptsubmit_legacy_payload_field_is_still_protected():
  with tempfile.TemporaryDirectory() as state:
    out = _run("userpromptsubmit.py",
               {"user_input": "DOB: 03/14/2019",
                "session_id": "t-legacy", "cwd": "/tmp"}, state)
    assert json.loads(out)["decision"] == "block", out


def test_userpromptsubmit_blocks_every_time_without_category_dedup():
  with tempfile.TemporaryDirectory() as state:
    payload = {"prompt": "MRN: 850001", "session_id": "t-dedup",
               "cwd": "/tmp"}
    assert json.loads(_run("userpromptsubmit.py", payload, state))["decision"] == "block"
    assert json.loads(_run("userpromptsubmit.py", payload, state))["decision"] == "block"
    payload["prompt"] = "MRN: 850002"
    assert json.loads(_run("userpromptsubmit.py", payload, state))["decision"] == "block"


def test_userpromptsubmit_clean_prompt_stays_silent():
  with tempfile.TemporaryDirectory() as state:
    for prompt in [
      "make the button blue please",
      "Use key = abcdefghijklmnop in this synthetic fixture",
      "Please change our support email to hello@offcall.com",
      "Update the footer contact to support@acme.com and 312-867-5309",
    ]:
      out = _run("userpromptsubmit.py",
                 {"prompt": prompt, "session_id": "t-clean", "cwd": "/tmp"},
                 state)
      assert out.strip() == "", (prompt, out)


def test_userpromptsubmit_blocks_high_confidence_assigned_secret():
  with tempfile.TemporaryDirectory() as state:
    value = "ab12cd34ef56gh78ij90kl12"
    out = _run(
      "userpromptsubmit.py",
      {"prompt": "API_KEY=" + value, "session_id": "t-assigned",
       "cwd": "/tmp"},
      state,
    )
    decision = json.loads(out)
    assert decision["decision"] == "block", out
    assert value not in out, out


def test_userpromptsubmit_obvious_synthetic_phi_stays_silent():
  with tempfile.TemporaryDirectory() as state:
    for prompt in ["email jane@example.com", "call 415-555-0132",
                   "use MRN: 123456 in the tutorial"]:
      out = _run("userpromptsubmit.py",
                 {"prompt": prompt, "session_id": "t-synthetic",
                  "cwd": "/tmp"}, state)
      assert out.strip() == "", (prompt, out)


# --------------------------------------------------------------------------- #
# PreToolUse — destructive commands, scope, commit hygiene
# --------------------------------------------------------------------------- #

def test_pretooluse_asks_on_rm_rf():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    out = _run("pretooluse.py",
               {"tool_name": "Bash",
                "tool_input": {"command": "rm -rf ./build"},
                "cwd": proj}, state)
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"


def test_pretooluse_scope_guard_catches_tilde_path():
  # The harness normally sends absolute paths, but a ~ path must not slip by.
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    out = _run("pretooluse.py",
               {"tool_name": "Write",
                "tool_input": {"file_path": "~/Documents/safe-start-test.txt",
                               "content": "x"},
                "cwd": proj}, state)
    assert "outside this project" in out, out


def test_pretooluse_allows_in_project_write():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    out = _run("pretooluse.py",
               {"tool_name": "Write",
                "tool_input": {"file_path": os.path.join(proj, "app.py"),
                               "content": "print('hi')"},
                "cwd": proj}, state)
    assert out.strip() == "", out


def test_pretooluse_write_cannot_modify_claude_control_plane():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    out = _run("pretooluse.py",
               {"tool_name": "Write",
                "tool_input": {
                  "file_path": os.path.expanduser("~/.claude/settings.json"),
                  "content": "{}",
                },
                "cwd": proj}, state)
    assert "outside this project" in out, out


def test_pretooluse_write_guards_phi_and_project_claude_settings():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    phi = _run("pretooluse.py",
               {"tool_name": "Edit",
                "tool_input": {"file_path": os.path.join(proj, "notes.txt"),
                               "new_string": "MRN: 8097342"},
                "cwd": proj}, state)
    assert "structured patient identifier" in phi, phi

    secret = _run(
      "pretooluse.py",
      {"tool_name": "Write",
       "tool_input": {"file_path": os.path.join(proj, "config.py"),
                      "content": "API_KEY=" + "h" * 32},
       "cwd": proj},
      state,
    )
    assert "approved secret manager" in secret, secret
    assert "stays out of Git" not in secret, secret

    settings = _run(
      "pretooluse.py",
      {"tool_name": "Write",
       "tool_input": {"file_path": os.path.join(
         proj, ".claude", "settings.local.json"
       ), "content": "{}"},
       "cwd": proj},
      state,
    )
    assert "controls Claude's permissions or hooks" in settings, settings

    for tool, payload in [
      ("Write", {"file_path": ".env", "content": "DEBUG=false\n"}),
      ("Edit", {"file_path": ".npmrc", "old_string": "x",
                "new_string": "y"}),
      ("NotebookEdit", {"notebook_path": "keys/server.pem",
                        "new_source": "placeholder"}),
    ]:
      out = _run(
        "pretooluse.py",
        {"tool_name": tool, "tool_input": payload, "cwd": proj},
        state,
      )
      assert "stores credentials or secrets" in out, (tool, out)


def test_pretooluse_native_reads_guard_outside_paths():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    payloads = [
      {"tool_name": "Read",
       "tool_input": {"file_path": "~/Documents/patients.csv"}},
      {"tool_name": "Glob",
       "tool_input": {"path": "../other-project", "pattern": "**/*.py"}},
      {"tool_name": "Grep",
       "tool_input": {"path": "/Volumes/Clinical", "pattern": "MRN"}},
    ]
    for payload in payloads:
      payload["cwd"] = proj
      out = _run("pretooluse.py", payload, state)
      assert "outside this project" in out, (payload, out)


def test_pretooluse_sensitive_in_project_reads_require_confirmation():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    payloads = [
      {"tool_name": "Read", "tool_input": {"file_path": ".env.local"}},
      {"tool_name": "Read", "tool_input": {"file_path": "keys/server.pem"}},
      {"tool_name": "Glob", "tool_input": {"path": proj,
                                              "pattern": ".env*"}},
      {"tool_name": "Grep", "tool_input": {"path": proj,
                                              "glob": "**/secrets/*.json",
                                              "pattern": "token"}},
    ]
    for payload in payloads:
      payload["cwd"] = proj
      out = _run("pretooluse.py", payload, state)
      assert "credentials or secrets" in out, (payload, out)

    out = _run(
      "pretooluse.py",
      {"tool_name": "Read", "tool_input": {"file_path": ".env.example"},
       "cwd": proj},
      state,
    )
    assert out.strip() == "", out


def test_pretooluse_allows_narrow_safe_start_template_read():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    path = os.path.expanduser(
      "~/.claude/skills/safe-start/templates/CLAUDE.md"
    )
    out = _run("pretooluse.py",
               {"tool_name": "Read", "tool_input": {"file_path": path},
                "cwd": proj}, state)
    assert out.strip() == "", out
    config = os.path.expanduser("~/.claude/safe-start/config.json")
    out = _run("pretooluse.py",
               {"tool_name": "Read", "tool_input": {"file_path": config},
                "cwd": proj}, state)
    assert out.strip() == "", out


def test_pretooluse_git_add_targeted_ignores_unrelated_junk():
  # `git add README.md` must not warn about an unrelated untracked .env;
  # `git add .` (which would stage it) must.
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    _init_repo(proj)
    with open(os.path.join(proj, "README.md"), "w") as fh:
      fh.write("hi\n")
    with open(os.path.join(proj, ".env"), "w") as fh:
      fh.write("X=1\n")
    targeted = _run("pretooluse.py",
                    {"tool_name": "Bash",
                     "tool_input": {"command": "git add README.md"},
                     "cwd": proj}, state)
    assert targeted.strip() == "", targeted
    broad = _run("pretooluse.py",
                 {"tool_name": "Bash",
                  "tool_input": {"command": "git add ."},
                  "cwd": proj}, state)
    assert "ask" in broad, broad


def test_pretooluse_git_add_scans_quoted_target_content():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    _init_repo(proj)
    path = os.path.join(proj, "service config.txt")
    with open(path, "w") as fh:
      fh.write("API_KEY=" + "a" * 32 + "\n")
    out = _run("pretooluse.py",
               {"tool_name": "Bash",
                "tool_input": {"command": "git add 'service config.txt'"},
                "cwd": proj}, state)
    assert "contain a credential" in out, out


def test_pretooluse_git_add_respects_subdirectory_cwd_and_git_c():
  with tempfile.TemporaryDirectory() as parent, \
       tempfile.TemporaryDirectory() as state:
    proj = os.path.join(parent, "repo")
    os.mkdir(proj)
    _init_repo(proj)
    sub = os.path.join(proj, "sub")
    os.mkdir(sub)
    with open(os.path.join(sub, "config.txt"), "w") as fh:
      fh.write("TOKEN=" + "d" * 32 + "\n")

    from_subdir = _run(
      "pretooluse.py",
      {"tool_name": "Bash", "tool_input": {"command": "git add config.txt"},
       "cwd": sub},
      state,
    )
    assert "contain a credential" in from_subdir, from_subdir

    via_c = _run(
      "pretooluse.py",
      {"tool_name": "Bash",
       "tool_input": {"command": "git -C repo add sub/config.txt"},
       "cwd": parent},
      state,
    )
    assert "contain a credential" in via_c, via_c


def test_pretooluse_tracks_cd_before_git_and_ignores_echoed_git_text():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    _init_repo(proj)
    sub = os.path.join(proj, "sub")
    os.makedirs(sub)
    with open(os.path.join(sub, "config.txt"), "w") as fh:
      fh.write("API_KEY=" + "k7" * 16 + "\n")

    out = _run(
      "pretooluse.py",
      {"tool_name": "Bash",
       "tool_input": {"command": "cd sub && git add config.txt"},
       "cwd": proj},
      state,
    )
    assert "contain a credential" in out, out

    echoed = _run(
      "pretooluse.py",
      {"tool_name": "Bash",
       "tool_input": {"command": "echo git add config.txt"},
       "cwd": proj},
      state,
    )
    assert echoed.strip() == "", echoed


def test_pretooluse_compound_add_commit_scans_intended_content():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    _init_repo(proj)
    path = os.path.join(proj, "clinical.txt")
    with open(path, "w") as fh:
      fh.write("MRN: 8097342\n")
    out = _run("pretooluse.py",
               {"tool_name": "Bash",
                "tool_input": {
                  "command": "git add clinical.txt && git commit -m save"
                }, "cwd": proj}, state)
    assert "structured patient identifier" in out, out


def test_pretooluse_commit_reads_staged_blob_not_worktree_copy():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    _init_repo(proj)
    path = os.path.join(proj, "config.txt")
    with open(path, "w") as fh:
      fh.write("TOKEN=" + "b" * 32 + "\n")
    subprocess.run(["git", "-C", proj, "add", "config.txt"], check=True)
    # The worktree is now clean-looking, but the index still has the credential.
    with open(path, "w") as fh:
      fh.write("safe placeholder\n")
    out = _run("pretooluse.py",
               {"tool_name": "Bash",
                "tool_input": {"command": "git commit -m save"},
                "cwd": proj}, state)
    assert "contain a credential" in out, out


def test_pretooluse_commit_a_scans_tracked_worktree_content():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    _init_repo(proj)
    path = os.path.join(proj, "config.txt")
    with open(path, "w") as fh:
      fh.write("safe placeholder\n")
    subprocess.run(["git", "-C", proj, "add", "config.txt"], check=True)
    subprocess.run(["git", "-C", proj, "commit", "-qm", "baseline"], check=True)
    with open(path, "w") as fh:
      fh.write("PASSWORD=" + "c" * 32 + "\n")
    out = _run("pretooluse.py",
               {"tool_name": "Bash",
                "tool_input": {"command": "git commit -am update"},
                "cwd": proj}, state)
    assert "contain a credential" in out, out


def test_pretooluse_commit_explicit_include_and_only_use_worktree_content():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    _init_repo(proj)
    for name in ("selected.txt", "other.txt"):
      with open(os.path.join(proj, name), "w") as fh:
        fh.write("baseline\n")
    subprocess.run(
      ["git", "-C", proj, "add", "selected.txt", "other.txt"], check=True
    )
    subprocess.run(["git", "-C", proj, "commit", "-qm", "baseline"], check=True)

    with open(os.path.join(proj, "selected.txt"), "w") as fh:
      fh.write("TOKEN=" + "e" * 32 + "\n")
    explicit = _run(
      "pretooluse.py",
      {"tool_name": "Bash",
       "tool_input": {"command": "git commit selected.txt -m selected"},
       "cwd": proj},
      state,
    )
    assert "contain a credential" in explicit, explicit
    included = _run(
      "pretooluse.py",
      {"tool_name": "Bash",
       "tool_input": {"command": "git commit --include selected.txt -m selected"},
       "cwd": proj},
      state,
    )
    assert "contain a credential" in included, included

    # A staged secret in an unrelated file is not part of --only's commit.
    with open(os.path.join(proj, "selected.txt"), "w") as fh:
      fh.write("safe selected change\n")
    with open(os.path.join(proj, "other.txt"), "w") as fh:
      fh.write("TOKEN=" + "f" * 32 + "\n")
    subprocess.run(["git", "-C", proj, "add", "other.txt"], check=True)
    only = _run(
      "pretooluse.py",
      {"tool_name": "Bash",
       "tool_input": {"command": "git commit --only selected.txt -m selected"},
       "cwd": proj},
      state,
    )
    assert only.strip() == "", only


def test_pretooluse_future_staging_replaces_old_staged_blob():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    _init_repo(proj)
    path = os.path.join(proj, "config.txt")
    with open(path, "w") as fh:
      fh.write("TOKEN=" + "g" * 32 + "\n")
    subprocess.run(["git", "-C", proj, "add", "config.txt"], check=True)
    with open(path, "w") as fh:
      fh.write("safe replacement\n")

    compound = _run(
      "pretooluse.py",
      {"tool_name": "Bash",
       "tool_input": {"command": "git add config.txt && git commit -m safe"},
       "cwd": proj},
      state,
    )
    assert compound.strip() == "", compound
    commit_all = _run(
      "pretooluse.py",
      {"tool_name": "Bash",
       "tool_input": {"command": "git commit -am safe"},
       "cwd": proj},
      state,
    )
    assert commit_all.strip() == "", commit_all


def test_pretooluse_git_add_flags_phi_filename_binary_and_large_file():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    _init_repo(proj)
    cases = [
      ("patient-export.csv", b"synthetic\n", "shouldn't track"),
      ("payload.dat", b"binary\x00payload", "appears to be binary"),
    ]
    for name, content, expected in cases:
      with open(os.path.join(proj, name), "wb") as fh:
        fh.write(content)
      out = _run("pretooluse.py",
                 {"tool_name": "Bash",
                  "tool_input": {"command": "git add %s" % name},
                  "cwd": proj}, state)
      assert expected in out, (name, out)

    large = os.path.join(proj, "archive.dat")
    with open(large, "wb") as fh:
      fh.seek(10 * 1024 * 1024)
      fh.write(b"x")
    out = _run("pretooluse.py",
               {"tool_name": "Bash",
                "tool_input": {"command": "git add archive.dat"},
                "cwd": proj}, state)
    assert "larger than 10 MiB" in out, out


def test_pretooluse_forced_add_expands_ignored_directories():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    _init_repo(proj)
    with open(os.path.join(proj, ".gitignore"), "w") as fh:
      fh.write("private/\n")
    private = os.path.join(proj, "private")
    os.makedirs(private)
    with open(os.path.join(private, "record.txt"), "w") as fh:
      fh.write("MRN: 8097342\n")

    out = _run(
      "pretooluse.py",
      {"tool_name": "Bash", "tool_input": {"command": "git add -f ."},
       "cwd": proj},
      state,
    )
    assert "private/record.txt" in out or "structured patient" in out, out


# --------------------------------------------------------------------------- #
# SessionStart — bypass warning
# --------------------------------------------------------------------------- #

def test_sessionstart_warns_when_permissions_bypassed():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    out = _run("sessionstart.py",
               {"cwd": proj, "permission_mode": "bypassPermissions"}, state)
    assert "Permissions are set to 'bypassPermissions'" in out, out
    assert "weakens one or more" in out, out


# --------------------------------------------------------------------------- #
# Standalone runner
# --------------------------------------------------------------------------- #

def _main() -> int:
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
  sys.exit(_main())
