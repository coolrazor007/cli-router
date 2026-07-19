# Repository Memory

This file preserves durable, high-signal lessons for maintainers and coding agents. It must never contain credential values, private keys, one-time device codes, or other secrets. Current facts belong in `STATE.md`; normative rules belong in `AGENTS.md`.

## GitHub Authentication Is Split

- Git transport and GitHub API authentication are independent. The repository remote uses SSH, so `ssh -T git@github.com` can succeed while `gh auth status` fails.
- `gh` API authentication should persist across sessions in the user's GitHub CLI configuration. In this WSL environment the credential store is `/home/razor/.config/gh/hosts.yml`.
- Never read, print, commit, or relay the token. Verify it safely with `gh auth status`; `gh auth token` may be used only with output suppressed when a script must test readability.
- An SSH success message exits nonzero because GitHub does not provide shell access. Judge `ssh -T git@github.com` by its message, not only its exit code.
- `gh auth setup-git` configures HTTPS Git credentials; it does not repair GitHub API authentication and is unnecessary for this repository's SSH remote.

### 2026-07-19 Authentication Incident

- The saved `gh` credential existed but GitHub rejected it as invalid. SSH push authentication remained healthy.
- A single browser/device flow with `gh auth login --hostname github.com --git-protocol ssh --web` replaced the invalid API credential. After the user authorized it, `gh auth status` and suppressed token-readability checks succeeded without another login.
- The correct response to a future failure is: check `gh auth status`, distinguish API auth from SSH auth and network failures, then reauthenticate only if GitHub reports the stored API credential invalid.
- Device authorization codes are transient secrets. Show them only to the user during the active flow; never save them in repository documentation, logs, commits, or chat summaries intended for reuse.

## Protected Main Changes the Release Order

- Direct pushes to `main` fail with `GH006`; this is expected protection, not an authentication failure.
- Start release metadata work on a release branch, push it, open a pull request, and wait for all required jobs.
- The repository commonly squash-merges pull requests. A tag created on the source commit before the squash would not point to the final `main` commit.
- Create the annotated version tag only after fetching the merged `main`. Verify the tag's peeled commit matches `origin/main` before pushing it.
- If an unpushed local tag points to the source commit, it is safe to recreate it at the identical merged tree. If a tag has already been pushed or a release may have published, stop and assess before rewriting it; do not force-update a public release tag casually.

## Publishing Mechanics

- A tag push alone does not publish CLI-Router. Publishing starts when a non-draft GitHub Release is published for the tag.
- `.github/workflows/publish.yml` performs tests, release-identity validation, build, Twine checks, artifact handoff, and PyPI trusted publishing.
- Trusted publishing uses GitHub OIDC. A normal release should not need `TWINE_PASSWORD`, `PYPI_API_TOKEN`, or a manual upload from a maintainer machine.
- Monitor the release workflow through the final `Publish CLI-Router to PyPI` job. A successful build job alone does not prove publication.
- Independently verify publication from a fresh virtual environment using the public PyPI index and the exact version. Check both `importlib.metadata.version("cli-router")` and `cli_router.__version__`, then run `cli-router check`.

## Local Verification Environment

- System Python may be protected by PEP 668 or lack package metadata and development tools. An uninstalled checkout can make `tests/test_version.py` fail even when the source versions agree.
- Use a temporary virtual environment, install `-e ".[dev]"`, and run tests, Ruff, mypy, coverage, CLI smoke checks, release identity, build, and Twine there.
- Build release artifacts in a fresh temporary output directory to avoid confusing stale `dist/` files with the current release.
- Network isolation can prevent build dependencies from resolving. Treat dependency-download failures separately from code failures and rerun with the environment's approved network mechanism.

## PyYAML Treats `on` as a YAML 1.1 Boolean

- PyYAML's safe loader parses an unquoted `on:` mapping key as boolean `true`, even though users naturally write conditional fallback policies with `on:`.
- CLI-Router normalizes boolean `true` back to the documented `on` key inside fallback policy mappings before validation. Preserve this normalization unless the entire loader is deliberately migrated to YAML 1.2 semantics.

## Compatibility Keys Need a Schema Bootstrap

- A router released before `requires_cli_router` existed can ignore that unknown top-level key, so the key alone cannot protect a safety-sensitive config from old binaries.
- Config version 2 is the bootstrap boundary: old routers that only accept version 1 fail immediately, while new routers require and evaluate `requires_cli_router`. Keep legacy version 1 support, including treating configs with no explicit version as v1.

## Fallback Policies Filter; Attempt Caps Count Processes

- A nonmatching conditional fallback policy is skipped, not treated as a failed attempt and not charged against `max_fallback_attempts`.
- After a fallback subprocess fails, its classified failure becomes the immediate trigger for later policies. Keep original-primary provenance separate from this immediate-trigger provenance so multi-hop chains remain auditable.

## Config Receipt Identity Is a Load-Time Snapshot

- Receipt identity must hash the exact source bytes read and the canonical effective merged config while loading. Never reread the source path when emitting a receipt: another process can replace the file between execution and receipt emission.
