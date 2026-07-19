# safe-start

A one-line install that turns Claude Code into a quiet safety net for people new
to coding — especially clinicians. It warns (never blocks) before anything that
would delete unrecoverable work, leak a secret or patient identifier, or send the
agent wandering out of your project — and it coaches Git as a savepoint system.

It is the **net** half of a pair. The **lesson** half is
`clinician-first-cli-session` — a one-time, live, break-and-recover onboarding.
Teach the habit once; `safe-start` keeps it on forever.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/dochobbs/OC_Safe_start/main/install.sh | bash
```

Installs into `~/.claude` (skill + guardrail hooks), preserving any settings and
hooks you already have. Remove anytime:

```bash
~/.claude/skills/safe-start/uninstall.sh
```

## What it does (13 features)

| # | Feature | Where |
|---|---------|-------|
| ① | On-ramp (git init, `.gitignore`, `/private`, starter CLAUDE.md) | SKILL + templates |
| ② | PHI + secret guard (warn+confirm) | hooks (`pretooluse`, `userpromptsubmit`) |
| ③ | Destructive-command guard | `hooks/pretooluse.py` |
| ④ | Git "undo" net (visible checkpoint commits) | SKILL |
| ⑤ | Verbosity dial (teaching ↔ terse) | SKILL + `config.json` |
| ⑥ | Plan-first nudge | SKILL |
| ⑦ | Sharing helper (private-by-default) | SKILL |
| ⑧ | Jargon translator | SKILL |
| ⑨ | "What did we build" recap + loose ends | SKILL + `sessionstart` |
| ⑩ | Scope guard (agent stays in the project) | `hooks/pretooluse.py` |
| ⑪ | Safety-net guard (don't disable permissions) | `hooks/sessionstart.py` |
| ⑫ | Orientation (only when it changed) | `hooks/sessionstart.py` |
| ⑬ | Git-state rescue (detached HEAD, stuck merge) | `hooks/sessionstart.py` + SKILL |

Plus: cloud-sync folder warning, private-repo default, commit hygiene
(junk/large files, `<<<<<<<` markers).

**Hardened against real foot-guns** (from a red-team pass): the destructive guard
also catches `find -delete`, `git branch -D`, and `git stash drop`; the scope
guard also catches out-of-project file access via Bash (`cat ~/Documents/…`,
even when the path is quoted) and covers `NotebookEdit`; the PHI/secret prompt
warning fires **once per category per session** (so re-using the same made-up
test data doesn't train alert fatigue); the installer **refuses to touch a
malformed or oddly-shaped `settings.json`** rather than overwrite it; and
safe-start's own state files are written owner-only (`0600`). A post-review
pass (2026-07-18) fixed the prompt-scan payload field (`user_input`), taught
the secret matcher env-var-style names (`OPENAI_API_KEY=…`) and modern
`sk-proj-` keys, added `git restore` to the destructive set, closed a `~`-path
scope gap, scoped `git add` warnings to the files actually being added, and
added hook-payload integration tests so a schema drift fails loudly in the
suite instead of silently in production. Known limits it does *not* catch: free-text PHI (a name
in a sentence), `rm -rf` hidden inside a script file, and — like any guard — YOLO
mode (`--dangerously-skip-permissions`) or a missing `python3` at runtime.

**Platform: macOS only, by design** — it matches the residency's Mac-based
30-day beginner guide (`ls`, the Command Line Tools flow, `~/Documents` paths).
Windows/Linux support isn't a goal for this audience.

## How it works

- **Guaranteed guards live in hooks** (deterministic — they run on every
  matching event, whether or not the model thinks of the skill; note the docs
  don't promise the confirm dialog appears under
  `--dangerously-skip-permissions`, which is why YOLO mode is listed as a known
  limit above). They return `permissionDecision: "ask"` with
  a specific reason, which fires Claude Code's native confirm dialog. Never a hard
  block — you always decide.
- **Coaching lives in `SKILL.md`** (on-ramp, plan-first, savepoints, sharing,
  jargon, recap, Git rescue).
- **Fail-open:** any error in a hook allows the action and logs to
  `~/.claude/safe-start/errors.log`. A safety bug can never block your work.

## Layout

```
safe-start/
├── SKILL.md                 # coaching layer
├── install.sh / uninstall.sh
├── hooks/
│   ├── pretooluse.py        # destructive / secret / scope / commit-hygiene
│   ├── userpromptsubmit.py  # PHI/secret in the typed prompt
│   ├── sessionstart.py      # orientation / safety-net / cloud-sync / git-state
│   └── lib/{detectors,common}.py
├── install/merge_settings.py  # surgical, idempotent settings merge
├── templates/{gitignore,CLAUDE.md}
└── tests/                     # 26 detector + 11 hook payload tests, plain python3
```

## Develop / test

```bash
python3 tests/test_detectors.py     # detection engine (no dependencies)
python3 tests/test_hooks.py         # hook payload integration (no dependencies)
```

The honest PHI line: this hard-catches secrets and flags structured identifiers
(SSN, MRN, DOB, phone, email), but it **cannot** catch free-text clinical
narrative. The trained habit — no real patient data — is the real defense.
