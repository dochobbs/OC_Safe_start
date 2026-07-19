"""Isolated installation, settings-transaction, and uninstall tests.

Every test supplies a temporary HOME.  The installer deliberately self-tests
only test_detectors.py and test_hooks.py; the sentinel below makes accidental
recursive execution of this lifecycle suite fail immediately.

Runnable two ways:
  python3 tests/test_lifecycle.py
  pytest tests/test_lifecycle.py
"""

import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile


if (
  __name__ == "__main__"
  and os.environ.get("SAFE_START_LIFECYCLE_RUNNING") == "1"
):
  print("lifecycle suite must not be run as an installer self-test")
  sys.exit(97)


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"
MERGER = ROOT / "install" / "merge_settings.py"
HOOK_SCRIPTS = ("pretooluse.py", "userpromptsubmit.py", "sessionstart.py")
MARKER = "skills/safe-start/hooks/"


def _env(home: Path, **updates) -> dict:
  env = dict(os.environ)
  for key in ("SAFE_START_SRC", "SAFE_START_REPO", "SAFE_START_REF"):
    env.pop(key, None)
  env.update({
    "HOME": str(home),
    "PYTHONDONTWRITEBYTECODE": "1",
    "SAFE_START_LIFECYCLE_RUNNING": "1",
  })
  env.update({key: str(value) for key, value in updates.items()})
  return env


def _run(argv, home: Path, *, env_updates=None, cwd=None, input_text=None,
         timeout=90):
  return subprocess.run(
    [str(arg) for arg in argv],
    cwd=str(cwd) if cwd else None,
    env=_env(home, **(env_updates or {})),
    input=input_text,
    capture_output=True,
    text=True,
    timeout=timeout,
  )


def _install(home: Path):
  return _run(
    ["bash", INSTALLER], home,
    env_updates={"SAFE_START_SRC": ROOT},
  )


def _settings(home: Path) -> Path:
  return home / ".claude" / "settings.json"


def _dest(home: Path) -> Path:
  return home / ".claude" / "skills" / "safe-start"


def _owned_commands(data: dict):
  commands = []
  for groups in data.get("hooks", {}).values():
    for group in groups:
      for hook in group.get("hooks", []):
        command = hook.get("command", "")
        if MARKER in command:
          commands.append(command)
  return commands


def _other_settings_fixture() -> dict:
  return {
    "model": "example-model",
    "permissions": {"allow": ["Bash(git status)"]},
    "hooks": {
      "PreToolUse": [{
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "printf other-pre-hook"}],
      }],
      "Stop": [{
        "matcher": "",
        "hooks": [{"type": "command", "command": "printf other-stop-hook"}],
      }],
    },
  }


def test_install_success_modes_and_direct_executable_uninstall():
  with tempfile.TemporaryDirectory(prefix="safe-start-home-") as raw_home:
    home = Path(raw_home)
    result = _install(home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "safe-start v1.1.0 is on" in result.stdout
    assert "guards registered and verified" in result.stdout

    dest = _dest(home)
    state_dir = home / ".claude" / "safe-start"
    config = state_dir / "config.json"
    assert dest.is_dir()
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert (dest / "LICENSE").is_file()
    assert os.access(dest / "uninstall.sh", os.X_OK)

    data = json.loads(_settings(home).read_text())
    assert len(_owned_commands(data)) == 3
    pre_matchers = [
      group.get("matcher")
      for group in data["hooks"]["PreToolUse"]
      if any(MARKER in hook.get("command", "")
             for hook in group.get("hooks", []))
    ]
    assert pre_matchers == ["Bash|Read|Glob|Grep|Write|Edit|NotebookEdit"]

    removed = _run([dest / "uninstall.sh"], home)
    assert removed.returncode == 0, removed.stdout + removed.stderr
    assert not dest.exists()
    assert not state_dir.exists()
    assert not _owned_commands(json.loads(_settings(home).read_text()))


def test_malformed_settings_aborts_and_restores_previous_install():
  with tempfile.TemporaryDirectory(prefix="safe-start-home-") as raw_home:
    home = Path(raw_home)
    old_dest = _dest(home)
    old_dest.mkdir(parents=True)
    marker = old_dest / "old-install-marker.txt"
    marker.write_text("keep the known-good install\n")

    settings = _settings(home)
    malformed = b'{"hooks": [\n'
    settings.write_bytes(malformed)
    os.chmod(settings, 0o640)

    result = _install(home)
    assert result.returncode != 0
    assert "not valid UTF-8 JSON" in result.stderr
    assert "restored the previous safe-start installation" in result.stdout
    assert marker.read_text() == "keep the known-good install\n"
    assert not (old_dest / "SKILL.md").exists()
    assert settings.read_bytes() == malformed
    assert stat.S_IMODE(settings.stat().st_mode) == 0o640
    assert not (settings.parent / "settings.json.safe-start.bak").exists()
    assert not (home / ".claude" / "safe-start").exists()


def test_non_regular_package_entry_is_rejected_before_live_replacement():
  with tempfile.TemporaryDirectory(prefix="safe-start-source-") as raw_base:
    base = Path(raw_base)
    home = base / "home"
    source = base / "source"
    home.mkdir()
    shutil.copytree(
      ROOT,
      source,
      ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    os.symlink("SKILL.md", source / "unexpected-link")

    old_dest = _dest(home)
    old_dest.mkdir(parents=True)
    marker = old_dest / "old-install-marker.txt"
    marker.write_text("known-good\n")

    result = _run(
      ["bash", INSTALLER], home,
      env_updates={"SAFE_START_SRC": source},
    )
    assert result.returncode != 0
    assert "package entries must be regular files or directories" in result.stdout
    assert "safe-start v1.1.0 is on" not in result.stdout
    assert marker.read_text() == "known-good\n"


def test_interrupts_cannot_split_install_transaction_state():
  with tempfile.TemporaryDirectory(prefix="safe-start-signals-") as raw_base:
    base = Path(raw_base)
    real_mv = shutil.which("mv")
    real_python = shutil.which("python3")

    # Signal immediately after the old installation's atomic rename. The
    # installer must finish coherently rather than deleting both old and new.
    move_home = base / "move-home"
    move_bin = base / "move-bin"
    move_home.mkdir()
    move_bin.mkdir()
    old_dest = _dest(move_home)
    old_dest.mkdir(parents=True)
    (old_dest / "known-good.txt").write_text("old\n")
    move_wrapper = move_bin / "mv"
    move_wrapper.write_text("""#!/bin/sh
case "$*" in
  *safe-start.backup.*safe-start*)
    if [ ! -e "$HOME/mv-signal-sent" ]; then
      : > "$HOME/mv-signal-sent"
      "$FAKE_REAL_MV" "$@" || exit $?
      kill -TERM "$PPID"
      exit 0
    fi
    ;;
esac
exec "$FAKE_REAL_MV" "$@"
""")
    os.chmod(move_wrapper, 0o755)
    move_result = _run(
      ["bash", INSTALLER], move_home,
      env_updates={
        "SAFE_START_SRC": ROOT,
        "FAKE_REAL_MV": real_mv,
        "PATH": str(move_bin) + os.pathsep + os.environ.get("PATH", ""),
      },
    )
    assert move_result.returncode == 0, move_result.stdout + move_result.stderr
    assert (_dest(move_home) / "SKILL.md").is_file()
    assert not (_dest(move_home) / "known-good.txt").exists()

    # Signal after settings registration returns success. Hook files and
    # registrations must remain together, never as dangling commands.
    merge_home = base / "merge-home"
    merge_bin = base / "merge-bin"
    merge_home.mkdir()
    merge_bin.mkdir()
    python_wrapper = merge_bin / "python3"
    python_wrapper.write_text("""#!/bin/sh
if [ "$2" = "add" ]; then
  case "$1" in
    */install/merge_settings.py)
      "$FAKE_REAL_PYTHON" "$@" || exit $?
      kill -TERM "$PPID"
      exit 0
      ;;
  esac
fi
exec "$FAKE_REAL_PYTHON" "$@"
""")
    os.chmod(python_wrapper, 0o755)
    merge_result = _run(
      ["bash", INSTALLER], merge_home,
      env_updates={
        "SAFE_START_SRC": ROOT,
        "FAKE_REAL_PYTHON": real_python,
        "PATH": str(merge_bin) + os.pathsep + os.environ.get("PATH", ""),
      },
    )
    assert merge_result.returncode == 130, merge_result.stdout + merge_result.stderr
    assert "new hook files" in merge_result.stdout
    assert (_dest(merge_home) / "hooks" / "pretooluse.py").is_file()
    assert len(_owned_commands(json.loads(_settings(merge_home).read_text()))) == 3


def test_update_is_convergent_and_uninstall_preserves_other_settings_hooks():
  with tempfile.TemporaryDirectory(prefix="safe-start-home-") as raw_home:
    home = Path(raw_home)
    settings = _settings(home)
    settings.parent.mkdir(parents=True)
    original = _other_settings_fixture()
    original_bytes = (json.dumps(original, indent=2) + "\n").encode()
    settings.write_bytes(original_bytes)
    os.chmod(settings, 0o644)

    first = _install(home)
    assert first.returncode == 0, first.stdout + first.stderr
    first_data = json.loads(settings.read_text())
    assert first_data["model"] == original["model"]
    assert first_data["permissions"] == original["permissions"]
    assert len(_owned_commands(first_data)) == 3
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    backup = settings.parent / "settings.json.safe-start.bak"
    assert backup.read_bytes() == original_bytes
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600

    settings_inode = settings.stat().st_ino
    second = _install(home)
    assert second.returncode == 0, second.stdout + second.stderr
    second_data = json.loads(settings.read_text())
    assert second_data == first_data
    assert len(_owned_commands(second_data)) == 3
    assert settings.stat().st_ino == settings_inode

    removed = _run([_dest(home) / "uninstall.sh"], home)
    assert removed.returncode == 0, removed.stdout + removed.stderr
    final = json.loads(settings.read_text())
    assert final == original
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600


def test_merge_settings_is_atomic_locked_private_and_shell_safe():
  with tempfile.TemporaryDirectory(prefix="safe start's home ") as raw_home:
    home = Path(raw_home)
    hooks_dir = home / ".claude" / "skills" / "safe-start" / "hooks"
    shutil.copytree(ROOT / "hooks", hooks_dir)
    settings = _settings(home)
    original = _other_settings_fixture()
    original_bytes = (json.dumps(original) + "\n").encode()
    settings.write_bytes(original_bytes)
    os.chmod(settings, 0o640)
    before_inode = settings.stat().st_ino

    result = _run([sys.executable, MERGER, "add", hooks_dir], home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert settings.stat().st_ino != before_inode
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    assert (settings.parent / "settings.json.safe-start.bak").read_bytes() == original_bytes
    assert stat.S_IMODE(
      (settings.parent / "settings.json.safe-start.bak").stat().st_mode
    ) == 0o600
    assert stat.S_IMODE(
      (settings.parent / ".safe-start-settings.lock").stat().st_mode
    ) == 0o600
    assert not list(settings.parent.glob(".settings.json.safe-start.*"))

    data = json.loads(settings.read_text())
    expected_dir = hooks_dir.resolve()
    for command in _owned_commands(data):
      parts = shlex.split(command)
      assert os.path.isabs(parts[0])
      assert Path(parts[0]).name.startswith("python3")
      assert Path(parts[0]).is_file()
      assert Path(parts[1]).parent.resolve() == expected_dir

    converged_inode = settings.stat().st_ino
    os.chmod(settings, 0o644)
    again = _run([sys.executable, MERGER, "add", hooks_dir], home)
    assert again.returncode == 0, again.stdout + again.stderr
    assert settings.stat().st_ino == converged_inode
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600

    # Concurrent updaters must serialize and converge to one owned hook/event.
    processes = [
      subprocess.Popen(
        [sys.executable, str(MERGER), "add", str(hooks_dir)],
        env=_env(home), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
      )
      for _ in range(4)
    ]
    for process in processes:
      stdout, stderr = process.communicate(timeout=30)
      assert process.returncode == 0, stdout + stderr
    assert len(_owned_commands(json.loads(settings.read_text()))) == 3

  with tempfile.TemporaryDirectory(prefix="safe-start-new-home-") as raw_home:
    home = Path(raw_home)
    result = _run([sys.executable, MERGER, "add", ROOT / "hooks"], home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert stat.S_IMODE(_settings(home).stat().st_mode) == 0o600


def test_merge_settings_refuses_lock_symlink_without_touching_target():
  with tempfile.TemporaryDirectory(prefix="safe-start-lock-") as raw_home:
    home = Path(raw_home)
    claude = home / ".claude"
    claude.mkdir()
    victim = home / "victim.txt"
    victim.write_text("leave me alone\n")
    os.chmod(victim, 0o640)
    os.symlink(victim, claude / ".safe-start-settings.lock")

    result = _run([sys.executable, MERGER, "add", ROOT / "hooks"], home)
    assert result.returncode != 0
    assert "is a symlink" in result.stderr
    assert victim.read_text() == "leave me alone\n"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o640
    assert not _settings(home).exists()


def test_uninstall_failure_preserves_recovery_scripts():
  with tempfile.TemporaryDirectory(prefix="safe-start-home-") as raw_home:
    home = Path(raw_home)
    installed = _install(home)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    settings = _settings(home)
    settings.write_text('{"hooks":')

    dest = _dest(home)
    result = _run([dest / "uninstall.sh"], home)
    assert result.returncode != 0
    assert "installed scripts are preserved" in result.stdout
    assert dest.is_dir()
    assert (dest / "uninstall.sh").is_file()
    assert (home / ".claude" / "safe-start" / "config.json").is_file()


def test_piped_installer_does_not_trust_cwd_and_uses_pinned_ref():
  with tempfile.TemporaryDirectory(prefix="safe-start-piped-") as raw_base:
    base = Path(raw_base)
    home = base / "home"
    cwd = base / "lookalike-cwd"
    fake_bin = base / "fake-bin"
    home.mkdir()
    (cwd / "hooks").mkdir(parents=True)
    fake_bin.mkdir()
    (cwd / "SKILL.md").write_text("MALICIOUS-CWD-MARKER\n")
    (cwd / "hooks" / "pretooluse.py").write_text("raise SystemExit(99)\n")

    fake_git = fake_bin / "git"
    fake_git.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$HOME/fake-git.log"
if [ "$1" = "clone" ]; then
  for last_arg do :; done
  cp -R "$FAKE_SAFE_START_SOURCE/." "$last_arg/"
  exit $?
fi
if [ "$1" = "-C" ] && [ "$3" = "rev-parse" ] && [ "$4" = "--verify" ]; then
  printf '%s\\n' 0123456789abcdef0123456789abcdef01234567
  exit 0
fi
exec "$FAKE_REAL_GIT" "$@"
""")
    os.chmod(fake_git, 0o755)

    path = str(fake_bin) + os.pathsep + os.environ.get("PATH", "")
    result = _run(
      ["bash"], home, cwd=cwd, input_text=INSTALLER.read_text(),
      env_updates={
        "PATH": path,
        "FAKE_SAFE_START_SOURCE": ROOT,
        "FAKE_REAL_GIT": shutil.which("git"),
        "SAFE_START_REPO": "https://invalid.invalid/no-network.git",
      },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "safe-start v1.1.0 is on" in result.stdout
    assert (_dest(home) / "SKILL.md").read_text() != "MALICIOUS-CWD-MARKER\n"
    calls = (home / "fake-git.log").read_text()
    assert "clone --depth 1 --branch v1.1.0 --" in calls
    assert "rev-parse --verify HEAD" in calls
    assert "rev-parse --verify v1.1.0^{commit}" in calls

    mismatch_home = base / "mismatch-home"
    mismatch_home.mkdir()
    mismatch = _run(
      ["bash"], mismatch_home, cwd=cwd, input_text=INSTALLER.read_text(),
      env_updates={
        "PATH": path,
        "FAKE_SAFE_START_SOURCE": ROOT,
        "FAKE_REAL_GIT": shutil.which("git"),
        "SAFE_START_REPO": "https://invalid.invalid/no-network.git",
        "SAFE_START_REF": "v9.9.9",
      },
    )
    assert mismatch.returncode != 0
    assert "Release mismatch: v9.9.9 contains VERSION 1.1.0" in mismatch.stdout


def _main() -> int:
  tests = [value for name, value in sorted(globals().items())
           if name.startswith("test_")]
  passed = 0
  failed = 0
  for test in tests:
    try:
      test()
      passed += 1
    except AssertionError as exc:
      failed += 1
      print("FAIL: %s  %s" % (test.__name__, exc))
    except Exception as exc:  # noqa: BLE001
      failed += 1
      print("ERROR: %s  %r" % (test.__name__, exc))
  print("\n%d passed, %d failed, %d total" %
        (passed, failed, passed + failed))
  return 1 if failed else 0


if __name__ == "__main__":
  sys.exit(_main())
