# safe-start

`safe-start` adds a quiet coaching skill and defense-in-depth hooks to Claude
Code for people new to coding, especially clinicians. It reduces common
accidental disclosure and data-loss mistakes. It is **not** a sandbox, a backup,
a secret manager, a data-loss guarantee, or a HIPAA compliance product.

It complements `clinician-first-cli-session`, the tool-agnostic, one-time
break-and-recover lesson. The installer below installs **safe-start only**: the
Claude Code skill and its hooks. It does not install the lesson or add hooks to
Codex.

## Install the pinned release

```bash
curl -fsSL https://raw.githubusercontent.com/dochobbs/OC_Safe_start/v1.1.0/install.sh | bash
```

The bootstrap and the package payload both default to the same `v1.1.0` tag.
The installer preserves unrelated Claude settings and hooks and must finish its
self-tests and registration before reporting success.

Remove it with:

```bash
bash ~/.claude/skills/safe-start/uninstall.sh
```

## Install the onboarding lesson

The lesson is a separate package. It adds the user-directed first-session skill
and its on-demand references, but does not install hooks or change Claude
settings. Version 1.3.1 keeps the first-class pause/return path and adds the
integrated user-turn contract: the user chooses build, learn, or explore before
file work; preview requests are hard gates; beginner actions stay one at a time;
and pushback ends the drill without pressure. Recovery uses a narrow read-only
Git check and never restores a file without approval plus a fresh safety check.

```bash
curl -fsSL https://raw.githubusercontent.com/dochobbs/OC_Safe_start/v1.3.1/install-lesson.sh | bash
```

The pinned bootstrap verifies the published lesson archive checksum before
replacing an existing lesson installation.

Remove only the lesson with either command:

```bash
bash ~/.claude/skills/clinician-first-cli-session/uninstall.sh
curl -fsSL https://raw.githubusercontent.com/dochobbs/OC_Safe_start/v1.3.1/uninstall-lesson.sh | bash
```

## What it does

| Control | Behavior |
|---|---|
| Prompt guard | Locally rejects high-confidence secrets and structured identifiers without echoing the value; the user removes them and resubmits |
| Tool guard | Asks before covered destructive commands, sensitive/out-of-project reads or writes, and risky Git operations |
| Project on-ramp | Confirms location, inventories first, merges ignore/project rules without clobbering, stages named files, scans the exact staged set, and verifies the first commit |
| Git coaching | Offers a reviewed savepoint before risky work and verifies it in `git log`; never claims an automatic checkpoint happened |
| Git rescue | Explains unfinished merges, conflicts, and detached HEAD states before offering choices |
| Orientation | Surfaces changed location, cloud-sync risk, weakened permissions, and loose ends when relevant |
| Sharing check | Reviews the exact share set, defaults GitHub to private, and states what the scan cannot prove |
| Teaching layer | Plans multi-step work, translates jargon, adjusts explanation volume, and gives plain-language recaps |

The prompt guard is intentionally stricter than the tool warnings: a detected
high-confidence value is rejected locally so it does not continue with the
prompt. Other covered tool actions normally use Claude Code's native ask flow.

## Security boundary and known limits

- Put only synthetic or appropriately de-identified data in an agent workspace.
  Keep any real or re-identifiable source outside it in an
  organization-approved encrypted system.
- `.gitignore` prevents ordinary Git tracking; it does not stop Claude or a
  local process from reading a file. An ignored `/private/` path is retained only
  as a legacy defensive exclusion, never as a place for PHI.
- Prefer environment variables, macOS Keychain, or an approved secret manager.
  If a project requires `.env`, never ask the agent to read, print, summarize,
  or echo it.
- Regexes cannot reliably identify free-text clinical narrative. A private Git
  repository is not an approved place for patient data.
- Native tool checks cover only hook events Claude Code emits. Bash inspection
  is best-effort: variables, scripts, aliases, nested interpreters, and future
  syntax can hide behavior.
- Scope warnings are not an OS sandbox. Keep Claude's permission prompts on and
  use least-privilege filesystem access for a real boundary.
- Git commits are restore points, not backups. They protect only reviewed work
  that was actually committed.

## Platform

The supported audience is macOS with Git and Python 3.9 or newer, matching the
residency's Mac-based beginner workflow. The lesson's habits transfer to Codex,
Windows, and Linux; these Claude Code hooks do not claim support there.

## License

safe-start is licensed under the Apache License, Version 2.0. See the bundled
`LICENSE` file.

## Layout

```text
safe-start/
├── SKILL.md
├── LICENSE
├── install.sh / uninstall.sh
├── hooks/
│   ├── pretooluse.py
│   ├── userpromptsubmit.py
│   ├── sessionstart.py
│   └── lib/{detectors,common}.py
├── install/merge_settings.py
├── templates/{gitignore,CLAUDE.md}
└── tests/
```

## Develop and test

```bash
python3 tests/test_detectors.py
python3 tests/test_hooks.py
python3 tests/test_lifecycle.py
```

In the private `offcall` source checkout, also run this from the repository root:

```bash
python3 scripts/build_skill_archives.py --check
```

That archive builder is a source-repository release check; it is not included in
the standalone public distribution copy. Do not publish or stage-demo a build
until the lifecycle, package, clean-install, and clean-uninstall release gates
also pass. The source-repository security model is the authority for those gates
and for every user-facing safety claim.
