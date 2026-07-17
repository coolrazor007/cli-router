#!/usr/bin/env python3
"""Validate that a release tag, package metadata, and runtime version agree."""

from __future__ import annotations

import os
import sys
from importlib.metadata import version

import cli_router


class ReleaseIdentityError(RuntimeError):
    """Raised when release version sources disagree."""


def validate_release(tag: str, package_version: str, runtime_version: str) -> None:
    expected_tag = f"v{package_version}"
    if tag != expected_tag:
        raise ReleaseIdentityError(f"release tag {tag!r} does not match {expected_tag!r}")
    if runtime_version != package_version:
        raise ReleaseIdentityError(
            f"runtime version {runtime_version!r} does not match package version {package_version!r}"
        )


def main() -> int:
    tag = os.environ.get("GITHUB_REF_NAME", "")
    package_version = version("cli-router")
    try:
        validate_release(tag, package_version, cli_router.__version__)
    except ReleaseIdentityError as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"release validation passed: {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
