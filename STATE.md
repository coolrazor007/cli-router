# Repository State

Last verified: 2026-07-19 (America/Los_Angeles)

This file records the current known state for maintainers and coding agents. Verify remote and time-sensitive facts before relying on them, and update this file when the state materially changes.

## Current Release

- Current published package version: `0.3.2`; package and runtime identity both report `0.3.2`.
- Annotated tag `v0.3.2` points to release commit `f09adecf79201db5eda4433d6d87683eff8ccfdb`, which was the verified `main` and `origin/main` tip when 0.3.2 was published.
- GitHub Release: <https://github.com/coolrazor007/cli-router/releases/tag/v0.3.2>
- PyPI package: `cli-router==0.3.2`, independently installed with cache disabled from the public PyPI index and verified to report metadata and runtime version `0.3.2`.
- The `Unreleased` changelog section is empty after the 0.3.2 release; the released changes are under the dated 0.3.2 section.
- Issue #1, the empty-stage false green, is closed as completed and represented in the 0.3.1 changelog.

## Repository and CI

- Default branch: `main`.
- `main` is protected and rejects direct pushes. Changes must be merged by pull request.
- Required pull-request jobs currently comprise Python 3.10, 3.11, 3.12, 3.13, and 3.14 tests plus `Quality and package`.
- The quality job runs Ruff, mypy, actionlint, branch-aware coverage with an 80% floor, package build, and Twine checks.
- The 0.3.2 release PR was #8 and all six required jobs passed before merge.

## Publishing

- `.github/workflows/publish.yml` is triggered by a published GitHub Release, not by a tag push alone.
- The publish workflow reruns the Python 3.10-3.14 test matrix and release-identity validation, builds and checks the distributions, and publishes through PyPI trusted publishing using GitHub OIDC.
- The successful 0.3.2 publish run is <https://github.com/coolrazor007/cli-router/actions/runs/29708635356>.
- Normal releases do not require a local PyPI API token or a manual `twine upload`.

## Local Environment

- Repository remote: `git@github.com:coolrazor007/cli-router.git`.
- Git pushes authenticate with the existing SSH key.
- GitHub API operations authenticate separately through `gh`.
- As of the last verification, `gh auth status` succeeds for `coolrazor007`, and `gh` can read its persistently stored credential from `/home/razor/.config/gh/hosts.yml`.
- Do not copy credential contents or transient device codes into this file.

## Verification Baseline

The 0.3.2 release was verified with:

- 235 tests passing locally.
- Ruff passing.
- mypy passing for 23 source files.
- Branch-aware coverage at 82%, meeting the configured 80% floor.
- CLI help, JSON output, and `cli-router check` smoke tests passing.
- Release identity validation passing for `v0.3.2`.
- Wheel and source distribution build and Twine checks passing.
- A built-wheel installation smoke test passing.
- A separate clean, cache-free installation of `cli-router==0.3.2` from public PyPI reporting metadata and runtime version `0.3.2`, with JSON version output succeeding and `cli-router check` returning `Configuration OK`.

## Known Blockers

- No known blocker remains for the 0.3.2 release.
