#!/usr/bin/env python3
"""Reject drift-agent changes outside the narrowly approved file set."""

from __future__ import annotations

import subprocess
import sys

ALLOWED_PATHS = frozenset(
    {
        "CHANGELOG.md",
        "cli_router/models.py",
        "tests/test_models.py",
    }
)


class PatchValidationError(RuntimeError):
    """Raised when an automated drift patch exceeds its approved scope."""


def validate_paths(paths: list[str]) -> None:
    changed = {path.strip() for path in paths if path.strip()}
    unexpected = sorted(changed - ALLOWED_PATHS)
    missing = sorted(ALLOWED_PATHS - changed)
    if unexpected:
        raise PatchValidationError(f"unexpected files changed: {', '.join(unexpected)}")
    if missing:
        raise PatchValidationError(f"missing files: {', '.join(missing)}")


def changed_paths() -> list[str]:
    tracked = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return sorted(set(tracked + untracked))


def main() -> int:
    try:
        paths = changed_paths()
        validate_paths(paths)
    except (PatchValidationError, subprocess.CalledProcessError) as exc:
        print(f"patch validation failed: {exc}", file=sys.stderr)
        return 1
    print("patch validation passed: " + ", ".join(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
