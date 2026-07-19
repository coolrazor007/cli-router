# Repository State

Last verified: 2026-07-19 (America/Los_Angeles)

This file records the current known state for maintainers and coding agents. Verify remote and time-sensitive facts before relying on them, and update this file when the state materially changes.

## Current Release

- Current package version: `0.3.1` in `pyproject.toml` and `cli_router/__init__.py`.
- Annotated tag `v0.3.1` points to release commit `a37b4960dd7467f19bdb767502c13dcdc522326f`, which was the verified `main` and `origin/main` tip when 0.3.1 was published.
- GitHub Release: <https://github.com/coolrazor007/cli-router/releases/tag/v0.3.1>
- PyPI package: `cli-router==0.3.1`, independently installed from the public PyPI index and verified to report metadata and runtime version `0.3.1`.
- The `Unreleased` changelog section is empty after the 0.3.1 release.
- Issue #1, the empty-stage false green, is closed as completed and represented in the 0.3.1 changelog.

## Repository and CI

- Default branch: `main`.
- `main` is protected and rejects direct pushes. Changes must be merged by pull request.
- Required pull-request jobs currently comprise Python 3.10, 3.11, 3.12, 3.13, and 3.14 tests plus `Quality and package`.
- The quality job runs Ruff, mypy, actionlint, branch-aware coverage with an 80% floor, package build, and Twine checks.
- The 0.3.1 release PR was #6 and all six required jobs passed before merge.

## Publishing

- `.github/workflows/publish.yml` is triggered by a published GitHub Release, not by a tag push alone.
- The publish workflow reruns the Python 3.10-3.14 test matrix and release-identity validation, builds and checks the distributions, and publishes through PyPI trusted publishing using GitHub OIDC.
- The successful 0.3.1 publish run is <https://github.com/coolrazor007/cli-router/actions/runs/29706059649>.
- Normal releases do not require a local PyPI API token or a manual `twine upload`.

## Local Environment

- Repository remote: `git@github.com:coolrazor007/cli-router.git`.
- Git pushes authenticate with the existing SSH key.
- GitHub API operations authenticate separately through `gh`.
- As of the last verification, `gh auth status` succeeds for `coolrazor007`, and `gh` can read its persistently stored credential from `/home/razor/.config/gh/hosts.yml`.
- Do not copy credential contents or transient device codes into this file.

## Verification Baseline

The 0.3.1 release was verified with:

- 185 tests passing locally.
- Ruff passing.
- mypy passing for 22 source files.
- Branch-aware coverage meeting the configured 80% floor.
- CLI help and `cli-router check` smoke tests passing.
- Release identity validation passing for `v0.3.1`.
- Wheel and source distribution build and Twine checks passing.
- A built-wheel installation smoke test passing.
- A separate clean installation of `cli-router==0.3.1` from public PyPI reporting metadata and runtime version `0.3.1`, with `cli-router check` returning `Configuration OK`.

## Known Blockers

- No known blocker remains for the 0.3.1 release.
