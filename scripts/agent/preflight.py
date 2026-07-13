#!/usr/bin/env python3
"""Runaway circuit breaker + kill switch for the agent loops.

Every agent workflow runs this first and only proceeds if it prints
``proceed=true`` to ``$GITHUB_OUTPUT``. Two independent brakes:

1. Kill switch — if an open issue labelled ``agent-halt`` exists, everything
   pauses. A human closes that issue to resume. (The breaker below opens it.)
2. Runaway cap — if agent-created issues + PRs in the last 24h reach
   ``DAILY_CAP``, the breaker opens the ``agent-halt`` issue and pauses.

Uses only the ``gh`` CLI with the workflow's ``GITHUB_TOKEN`` (needs
``issues: read/write``) — no PAT, no admin, fully reversible. Portable across
macOS/Linux self-hosted runners (no GNU-only ``date`` flags).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

HALT_LABEL = "agent-halt"
CREATED_LABEL = "agent-created"


def gh_json(args: list[str]) -> object:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout
    return json.loads(out) if out.strip() else None


def ensure_labels(repo: str) -> None:
    # Idempotently create the labels the loops rely on, so a first run does not
    # fail querying a label that does not exist yet.
    for name, color, desc in (
        (HALT_LABEL, "b60205", "Pauses all agent loops while open"),
        (CREATED_LABEL, "5319e7", "Opened by an agent loop"),
    ):
        subprocess.run(
            ["gh", "label", "create", name, "-R", repo, "--color", color, "--description", desc, "--force"],
            capture_output=True,
            text=True,
            check=False,
        )


def emit(proceed: bool, reason: str) -> int:
    print(f"circuit-breaker: proceed={proceed} — {reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"proceed={'true' if proceed else 'false'}\n")
            handle.write(f"reason={reason}\n")
    return 0


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    cap = int(os.environ.get("DAILY_CAP", "10"))
    ensure_labels(repo)

    # 1) Kill switch.
    halted = gh_json(["issue", "list", "-R", repo, "--label", HALT_LABEL, "--state", "open", "--json", "number"])
    if halted:
        return emit(False, f"paused: open {HALT_LABEL} issue #{halted[0]['number']} (close it to resume)")

    # 2) Runaway cap: count agent-created issues AND PRs in the last 24h.
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = gh_json(
        ["api", "-X", "GET", "search/issues", "-f", f"q=repo:{repo} label:{CREATED_LABEL} created:>={since}"]
    )
    count = int(result.get("total_count", 0)) if isinstance(result, dict) else 0
    if count >= cap:
        subprocess.run(
            [
                "gh", "issue", "create", "-R", repo,
                "--label", HALT_LABEL,
                "--title", f"Agent loops paused: {count} agent items in 24h (cap {cap})",
                "--body",
                f"The runaway circuit breaker tripped: {count} `{CREATED_LABEL}` issues/PRs were created "
                f"in the last 24h, at or over the cap of {cap}.\n\n"
                f"All agent workflows are paused while this issue is open. Review the recent agent activity, "
                f"then **close this issue to resume**.",
            ],
            check=True,
        )
        return emit(False, f"tripped: {count} agent items in 24h >= cap {cap}; opened {HALT_LABEL} issue")

    return emit(True, f"ok: {count}/{cap} agent items in last 24h")


if __name__ == "__main__":
    sys.exit(main())
