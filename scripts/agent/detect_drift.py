#!/usr/bin/env python3
"""Detect actionable provider model-list drift for the drift-watchdog loop.

Runs live discovery for every provider that has a safe discovery command and
compares it against the shipped ``DEFAULT_MODELS``. The *actionable* signal is a
shipped fallback model that no longer exists live (e.g. a retired ``gpt-5``) —
not "the CLI now lists more models", because ``DEFAULT_MODELS`` is intentionally
a curated subset. A newly-appeared flagship (the top live model we don't ship)
is reported as an advisory only.

Emits a JSON verdict to stdout:
    {"drift": bool, "deltas": {provider: {removed, live, static, new_flagship}}}

Exit code: 0 always (the workflow decides what to do with the verdict); use
``--exit-code`` to instead exit 1 when drift is found (handy for `if:` guards).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from cli_router.models import DEFAULT_MODELS, MODEL_LIST_COMMANDS, probe_models


def detect(timeout: float) -> dict:
    deltas: dict[str, dict] = {}
    for provider in MODEL_LIST_COMMANDS:  # only providers with a live discovery command
        probe = probe_models(provider, subprocess.run, timeout=timeout)
        live = probe.models
        if not live:
            # Discovery produced nothing (CLI down / transient). Absence of a
            # signal is not drift — skip rather than raise a false alarm.
            continue
        static = list(DEFAULT_MODELS.get(provider, []))
        removed = [m for m in static if m not in live]
        new_flagship = live[0] if live and live[0] not in static else None
        if removed or new_flagship:
            deltas[provider] = {
                "removed": removed,
                "new_flagship": new_flagship,
                "live": live,
                "static": static,
            }
    # Drift is actionable only when a shipped model was removed; a new flagship
    # alone is advisory (surface it, but do not force a change).
    actionable = any(d["removed"] for d in deltas.values())
    return {"drift": actionable, "deltas": deltas}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect provider model-list drift.")
    parser.add_argument("--timeout", type=float, default=15.0, help="Per-provider discovery timeout (s).")
    parser.add_argument("--exit-code", action="store_true", help="Exit 1 when actionable drift is found.")
    args = parser.parse_args(argv)

    verdict = detect(args.timeout)
    print(json.dumps(verdict, indent=2))
    if args.exit_code and verdict["drift"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
