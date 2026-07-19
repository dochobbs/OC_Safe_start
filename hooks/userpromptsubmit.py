#!/usr/bin/env python3
"""safe-start UserPromptSubmit guard.

Scans the message the user just typed for secrets or PHI identifiers. This is
the classic first-timer mistake: pasting a chart snippet or an API key straight
into the prompt. It NEVER blocks or drops the message — it injects a short note
asking Claude to pause, point it out warmly, and confirm before proceeding
(offering a made-up placeholder if it's real patient data).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(
  0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
)

import common  # noqa: E402
import detectors as d  # noqa: E402


def main() -> None:
  data = common.read_input()
  # Claude Code sends the typed message as "user_input" (see the hooks docs);
  # the fallbacks keep this working if the payload schema drifts again.
  prompt = (data.get("user_input") or data.get("prompt")
            or data.get("user_prompt") or "")
  session_id = data.get("session_id", "") or ""

  secs = d.find_secrets(prompt)
  phi = d.find_phi_identifiers(prompt)
  if not secs and not phi:
    common.allow()

  finding = secs[0] if secs else phi[0]

  # Warn once per session per category. Re-flagging the same made-up test data
  # on every reuse would just train people to dismiss the warning.
  if common.seen_this_session(session_id, "%s:%s" % (finding.kind, finding.label)):
    common.allow()

  if secs:
    kind = "a secret or credential"
  else:
    kind = "possible patient information (%s)" % finding.label

  note = (
    "\n[safe-start] The user's message appears to contain %s. %s "
    "Before you use it, gently point this out and confirm they want to "
    "proceed. If it's real patient data, offer to continue with a made-up "
    "placeholder instead. Do not echo the sensitive value back.\n"
    % (kind, finding.reason)
  )
  common.context(note)


if __name__ == "__main__":
  common.guard(main, "userpromptsubmit")
