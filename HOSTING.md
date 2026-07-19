# Publishing safe-start

**Distribution repository:** `https://github.com/dochobbs/OC_Safe_start`

**Release target:** `v1.1.0`. The version tag, not the mutable `main` branch, is
the supported installation boundary. Do not announce the release as available
until that public tag exists and every release gate below passes.

The canonical command for this release is:

```bash
curl -fsSL https://raw.githubusercontent.com/dochobbs/OC_Safe_start/v1.1.0/install.sh | bash
```

Source of truth remains this `offcall` checkout. The public repository is the
reviewed distribution copy.

## Version-pinning contract

The bootstrap script above is fetched from `v1.1.0`, and its default payload
fetch must resolve the same `v1.1.0` tag. Never fetch a pinned bootstrap and then
silently install payload files from `main`.

Local development may use an explicit `SAFE_START_SRC` checkout. Release and
stage-demo instructions must use the pinned public tag so the tested bytes and
the installed bytes can be compared.

## Public repository shape

Copy the contents of `skills/safe-start/` to the public repository root. The
root must contain `VERSION`, `install.sh`, `uninstall.sh`, `SKILL.md`, `hooks/`,
`install/`, `templates/`, `tests/`, and user documentation. Do not include Git
metadata, caches, local state, credentials, sensitive fixtures, or real patient
data.

The installer installs **safe-start only** into `~/.claude`: the coaching skill,
Claude Code hooks, templates, and owned state. It does not install
`clinician-first-cli-session` or add enforcement to Codex.

## Release gates

Treat the checklist below as the public distribution contract. The private
source checkout also maintains the fuller security model used during review.
Before creating or announcing the tag:

1. Run detector, payload, lifecycle, and package-validation tests on the oldest
   supported Apple Python and the current development Python.
2. From the `offcall` source-repository root, verify the generated archives and
   executable modes:

   ```bash
   python3 scripts/build_skill_archives.py --check
   ```

   This source-only builder is not part of the standalone public copy.
3. Install into a fresh isolated home and confirm every expected hook is active,
   state/config permissions are private, and the installed version is `1.1.0`.
4. Exercise malformed/unwritable settings failures and verify the prior install
   and settings remain intact with a nonzero exit and no success banner.
5. Exercise uninstall success and forced deregistration failure. Never delete
   hook scripts while owned registrations remain active.
6. Copy the reviewed package to the public repository, create the `v1.1.0` tag,
   push it, and compare that tag with the reviewed package.
7. Run the pinned one-liner on a clean Mac, then run the documented uninstall:

   ```bash
   bash ~/.claude/skills/safe-start/uninstall.sh
   ```

8. Re-run the pinned install once to test the supported update/reinstall path.
   Confirm unrelated Claude settings and hooks remain unchanged.

Only then call the release live or use it in a stage demo.

## Test without touching the real Claude home

After the public tag exists:

```bash
HOME=$(mktemp -d) bash -c 'curl -fsSL https://raw.githubusercontent.com/dochobbs/OC_Safe_start/v1.1.0/install.sh | bash'
```

Inspect the isolated home rather than trusting the success banner alone. Verify
the installed `VERSION`, hook registrations, file modes, and uninstall result.

## Publish a later release

Choose a new semantic version, update `VERSION` and every pinned bootstrap and
payload reference together, repeat the full release gates, then publish a new
immutable tag. Update user-facing install commands only after that tag has been
verified. Never repoint an existing tag or advise users to install from `main`.

## Security notes

- A public tag is executable code. Review the exact tag before telling users to
  pipe it to a shell; a tag is versioned, but an unsigned tag does not eliminate
  maintainer-account compromise.
- `.gitignore`, prompt regexes, and advisory hooks do not make PHI safe. Use only
  synthetic or appropriately de-identified data in an agent workspace.
- Keep real or re-identifiable source data in an organization-approved encrypted
  system outside the workspace and model context.
- macOS is the supported v1 platform.
