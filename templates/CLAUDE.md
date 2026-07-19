# Project notes for Claude

I'm new to coding and I'm using Claude Code with the **safe-start** guardrails on.

## How I like to work
- **Verbosity: teaching** — explain what you're doing and why, in plain
  language, and define any jargon the first time you use it. (When I'm
  comfortable, I'll change this line to `Verbosity: just do it` for terser help.)
- Plan before building anything non-trivial — show me a short plan first.
- Before anything risky, offer a reviewed Git savepoint. Stage named files only,
  scan the staged contents, commit with my approval, and verify it in `git log`.

## Rules
- **No real or re-identifiable patient data in this workspace or in prompts.**
  Use synthetic or appropriately de-identified data only. Keep any sensitive
  source outside this workspace in an organization-approved encrypted system.
- Keep secrets out of code, prompts, and agent-readable files. Prefer environment
  variables, Keychain, or an approved secret manager. If this project requires
  `.env`, never read, print, summarize, or echo it.
- Treat `.gitignore` as Git hygiene only, not an access or privacy boundary.
- Ask before doing anything that deletes files or can't be undone.
