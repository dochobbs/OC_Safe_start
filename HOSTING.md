# Hosting safe-start for the one-line install

**Status: DONE (2026-07-18).** Hosted at the public repo
`https://github.com/dochobbs/OC_Safe_start`; the one-liner is live:

```bash
curl -fsSL https://raw.githubusercontent.com/dochobbs/OC_Safe_start/main/install.sh | bash
```

The steps below are kept for reference (re-hosting, or moving to another
account/domain). Source of truth stays in the private `offcall` repo — edit
there, then copy to the public repo (see "Updating later").

## How the installer finds the package

`install.sh` looks for the skill in this order:
1. `SAFE_START_SRC` env var (a local path) — used for local testing.
2. `install.sh` sitting next to a `SKILL.md` (a local checkout).
3. Otherwise it **`git clone`s** `SAFE_START_REPO` (default: the `REPLACE_ME`
   placeholder) into a temp dir and installs from there.

When a user runs `curl … | bash`, none of 1–2 apply — so path 3 must point at a
real repo. That's the one thing to set up.

## Setup (once)

1. **Create a public GitHub repo** — e.g. `github.com/<you>/safe-start`.
   Public so `git clone` / `curl` work without auth. It contains no secrets.

2. **Put this package at the repo root.** The repo's top level must be the
   *contents* of `skills/safe-start/` — so `install.sh`, `SKILL.md`, `hooks/`,
   `install/`, `templates/`, `tests/`, `uninstall.sh`, `README.md` all sit at the
   root (not nested under a `safe-start/` folder). The installer copies the repo
   root into `~/.claude/skills/safe-start/`.

   ```bash
   # from this repo:
   cp -R skills/safe-start/. /path/to/your/new/safe-start-repo/
   cd /path/to/your/new/safe-start-repo
   git init && git add -A && git commit -m "safe-start v1" && git branch -M main
   git remote add origin git@github.com:<you>/safe-start.git   # SSH
   git push -u origin main
   ```

3. **Set the default repo URL** in `install.sh` (line ~39): replace
   `https://github.com/REPLACE_ME/safe-start.git` with your repo's clone URL.
   (Use the **HTTPS** clone URL here — the *installer* runs on a stranger's
   machine that won't have your SSH key. HTTPS clone of a public repo needs no
   auth.) Commit and push that change too.

4. **The one-liner** users paste (raw `install.sh` from GitHub):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/dochobbs/OC_Safe_start/main/install.sh | bash
   ```

## Test it (on a clean machine or a fresh `HOME`)

```bash
# simulate a clean install without touching your real ~/.claude:
HOME=$(mktemp -d) bash -c 'curl -fsSL https://raw.githubusercontent.com/dochobbs/OC_Safe_start/main/install.sh | bash'
```
Expect: "detection + hook self-test passed", "guards registered", the summary box.

## Updating later

Change the skill here → copy to the repo → commit + push. Users re-run the same
one-liner to update; the install is **idempotent** and preserves their other
hooks, so re-running is always safe. Consider tagging releases (`v1`, `v2`) and
pointing the raw URL at a tag if you want stability over `main`.

## Notes

- Pairs with the **lesson** (`clinician-first-cli-session`), whose send-off runs
  this installer. Until this is hosted, the lesson's send-off correctly says
  "a permanent version is coming" instead of offering a dead link.
- macOS only, by design (see the README).
- Nothing in this package should ever contain real keys or PHI — it's public.
