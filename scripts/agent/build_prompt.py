#!/usr/bin/env python3
"""Turn a drift verdict (from detect_drift.py) into a bounded agent prompt.

Kept tiny and deterministic so the agent gets one narrow instruction: update the
stale ``DEFAULT_MODELS`` entries, the matching test, and the changelog — nothing
else. Usage: ``build_prompt.py drift.json`` prints the prompt to stdout.
"""

from __future__ import annotations

import json
import sys


def build(verdict: dict) -> str:
    lines = []
    for provider, delta in verdict.get("deltas", {}).items():
        removed = ", ".join(delta.get("removed") or []) or "(none)"
        flagship = delta.get("new_flagship") or "(none)"
        live = ", ".join(delta.get("live") or [])
        lines.append(
            f"- {provider}: shipped fallback lists retired model(s) [{removed}]; "
            f"new flagship [{flagship}]; current live models: [{live}]"
        )
    findings = "\n".join(lines)
    return (
        "Provider model-list drift was detected by `cli-router doctor`:\n\n"
        f"{findings}\n\n"
        "Make ONLY this change:\n"
        "1. In `cli_router/models.py`, update the affected provider entries in "
        "`DEFAULT_MODELS` to a curated list drawn from the current live models — "
        "keep the current flagship first, drop any retired model, and keep the "
        "list to the notable models (roughly the top 3-4), matching the existing "
        "style.\n"
        "2. Update the corresponding assertion(s) in `tests/test_models.py`.\n"
        "3. Add one bullet under the `## Unreleased` heading in `CHANGELOG.md`.\n"
        "4. Run `python -m pytest -q` and ensure it passes.\n\n"
        "Do not touch any other files, models, or behaviour."
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: build_prompt.py <drift.json>", file=sys.stderr)
        return 2
    verdict = json.load(open(sys.argv[1], encoding="utf-8"))
    print(build(verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
