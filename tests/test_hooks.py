"""Integration tests for the safe-start hooks: realistic payloads in, output out.

The detectors can be perfect while a hook reads the wrong payload field and
silently never fires — exactly the bug that motivated this file (the prompt
scan read "user_prompt"; Claude Code sends "user_input"). Each test pipes a
realistic Claude Code hook payload into the hook script as a subprocess and
asserts on stdout, so a payload-schema drift fails loudly here instead of
silently in production.

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
import time

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


# --------------------------------------------------------------------------- #
# UserPromptSubmit — the prompt PHI/secret scan
# --------------------------------------------------------------------------- #

def test_userpromptsubmit_flags_phi_in_user_input():
  # Claude Code sends the typed message as "user_input".
  with tempfile.TemporaryDirectory() as state:
    out = _run("userpromptsubmit.py",
               {"user_input": "pt MRN: 8675309, needs follow-up",
                "session_id": "t-phi", "cwd": "/tmp"}, state)
    assert "[safe-start]" in out and "medical record" in out, out


def test_userpromptsubmit_flags_secret_in_user_input():
  with tempfile.TemporaryDirectory() as state:
    out = _run("userpromptsubmit.py",
               {"user_input": "use AKIAIOSFODNN7EXAMPLE for the bucket",
                "session_id": "t-sec", "cwd": "/tmp"}, state)
    assert "[safe-start]" in out and "secret or credential" in out, out


def test_userpromptsubmit_warns_once_per_session():
  with tempfile.TemporaryDirectory() as state:
    payload = {"user_input": "MRN: 555001", "session_id": "t-dedup",
               "cwd": "/tmp"}
    assert "[safe-start]" in _run("userpromptsubmit.py", payload, state)
    assert _run("userpromptsubmit.py", payload, state).strip() == ""


def test_userpromptsubmit_clean_prompt_stays_silent():
  with tempfile.TemporaryDirectory() as state:
    out = _run("userpromptsubmit.py",
               {"user_input": "make the button blue please",
                "session_id": "t-clean", "cwd": "/tmp"}, state)
    assert out.strip() == "", out


def test_state_dir_override_keeps_state_out_of_home():
  with tempfile.TemporaryDirectory() as state:
    out = _run("userpromptsubmit.py",
               {"user_input": "MRN: 424242 again", "session_id": "t-state",
                "cwd": "/tmp"}, state)
    assert "[safe-start]" in out, out
    assert any(f.startswith("seen-") for f in os.listdir(state))


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


def test_pretooluse_git_add_targeted_ignores_unrelated_junk():
  # `git add README.md` must not warn about an unrelated untracked .env;
  # `git add .` (which would stage it) must.
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    subprocess.run(["git", "init", "-q", proj], check=True)
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


# --------------------------------------------------------------------------- #
# SessionStart — bypass warning, state housekeeping
# --------------------------------------------------------------------------- #

def test_sessionstart_warns_when_permissions_bypassed():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    out = _run("sessionstart.py",
               {"cwd": proj, "permission_mode": "bypassPermissions"}, state)
    assert "Permissions are set to 'bypassPermissions'" in out, out


def test_sessionstart_prunes_stale_session_state():
  with tempfile.TemporaryDirectory() as proj, \
       tempfile.TemporaryDirectory() as state:
    old = os.path.join(state, "seen-old.json")
    fresh = os.path.join(state, "seen-new.json")
    for p in (old, fresh):
      with open(p, "w") as fh:
        fh.write("{}")
    stale = time.time() - 8 * 86400
    os.utime(old, (stale, stale))
    _run("sessionstart.py", {"cwd": proj}, state)
    assert not os.path.exists(old), "stale session state should be pruned"
    assert os.path.exists(fresh), "recent session state must be kept"


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
