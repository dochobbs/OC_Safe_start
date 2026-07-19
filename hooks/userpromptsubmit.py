#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""safe-start UserPromptSubmit guard.

Scans the message the user just typed for secrets or structured PHI
identifiers. High-confidence matches are rejected locally with Claude Code's
UserPromptSubmit block decision, so the sensitive prompt never enters model
context. The reason is generic and never echoes the matched value.
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
  # Current Claude Code uses "prompt". Keep legacy fallbacks so an older client
  # remains protected during upgrades.
  prompt = (data.get("prompt") or data.get("user_input")
            or data.get("user_prompt") or "")

  secs = d.find_secrets(prompt, high_confidence_only=True)
  phi = d.find_phi_identifiers(prompt)
  if not secs and not phi:
    common.allow()

  if secs:
    reason = (
      "safe-start blocked this prompt because it appears to contain a secret "
      "or credential. Remove it, rotate it if it may be real, and retry with a "
      "placeholder. The detected value was not sent to Claude."
    )
  else:
    reason = (
      "safe-start blocked this prompt because it appears to contain a "
      "structured patient identifier. Remove or replace it with clearly "
      "synthetic data, then retry. The detected value was not sent to Claude."
    )
  common.block_prompt(reason)


if __name__ == "__main__":
  common.guard(main, "userpromptsubmit")
