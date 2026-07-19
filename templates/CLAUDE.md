# Project notes for Claude

I'm new to coding and I'm using Claude Code with the **safe-start** guardrails on.

## How I like to work
- **Verbosity: teaching** — explain what you're doing and why, in plain
  language, and define any jargon the first time you use it. (When I'm
  comfortable, I'll change this line to `Verbosity: just do it` for terser help.)
- Plan before building anything non-trivial — show me a short plan first.
- Make a Git savepoint (a commit) before anything risky, so we can always go back.

## Rules
- **No real patient data.** Use made-up names and details. Anything with real
  data goes in the `/private/` folder, which Git ignores.
- Keep secrets (API keys, passwords) out of code and prompts — use a `.env` file.
- Ask before doing anything that deletes files or can't be undone.
