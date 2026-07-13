#!/usr/bin/env bash
#
# Apply the drift fix using a LOCALLY-AUTHENTICATED agent CLI on the self-hosted
# runner. This is the one piece that depends on your Mothership setup — the CLI
# must already be logged in (subscription auth in $HOME) and allowed to edit
# files headlessly.
#
# TUNE THE INVOCATION BELOW to whatever your installed CLI/version accepts. The
# defaults target Claude Code in headless, edit-capable mode. Set AGENT_CMD in
# the environment to override entirely (it receives the prompt on stdin).
#
# Usage: apply_drift_fix.sh <prompt-file>
set -euo pipefail

PROMPT_FILE="${1:?usage: apply_drift_fix.sh <prompt-file>}"
PROMPT="$(cat "$PROMPT_FILE")"

if [[ -n "${AGENT_CMD:-}" ]]; then
  # Fully custom: your command reads the prompt from stdin.
  printf '%s' "$PROMPT" | bash -c "$AGENT_CMD"
else
  # Default: Claude Code, headless, auto-accepting file edits.
  # Verify these flags against your installed version before trusting it.
  claude -p --permission-mode acceptEdits "$PROMPT"
fi

echo "--- git diff after agent run ---"
git --no-pager diff --stat
