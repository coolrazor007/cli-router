# Release Runbook

This is the canonical release procedure for CLI-Router. It is written for maintainers and coding agents and assumes the release is semantically appropriate under the project's versioning policy.

Replace every `X.Y.Z`, `vX.Y.Z`, `RUN_ID`, and date placeholder below with the actual release values. Use fresh, nonexisting temporary directories when reusing the command examples.

## Release Architecture

The release path is deliberately staged:

1. Merge version and changelog changes through protected `main`.
2. Create and push an annotated tag on the merged `main` commit.
3. Publish a GitHub Release for that tag.
4. Let `.github/workflows/publish.yml` test, build, and publish to PyPI through trusted publishing.
5. Independently install the exact public PyPI version.

Pushing a tag alone does not trigger package publication.

## 1. Preflight

Confirm the worktree, remote state, version availability, and both authentication paths:

```bash
git status --short --branch
git fetch origin main --tags
git remote -v
gh auth status
ssh -T git@github.com
git tag --list 'vX.Y.Z'
```

Notes:

- The repository remote uses SSH; `gh` uses a separate API credential.
- GitHub's successful SSH greeting normally exits nonzero because shell access is disabled.
- If `gh auth status` reports an invalid stored credential, run `gh auth login --hostname github.com --git-protocol ssh --web` once and verify `gh auth status` afterward. Do not save the one-time device code.
- Confirm the target version is absent from GitHub Releases and PyPI before proceeding.

## 2. Prepare Release Metadata on a Branch

Create the branch before committing so local `main` never diverges from protected `origin/main`:

```bash
git switch -c release/X.Y.Z origin/main
```

Update:

- `pyproject.toml` project version.
- `cli_router/__init__.py` runtime version.
- `CHANGELOG.md`: leave a fresh empty `Unreleased` section and move the accumulated entries under `X.Y.Z - YYYY-MM-DD`.
- `STATE.md` only after the release outcome is known; do not claim publication before verification.

Review the release delta for accidental public command or configuration incompatibilities. Patch releases must not knowingly introduce incompatible public contracts.

## 3. Verify Locally

Use an isolated virtual environment. A system interpreter may be PEP 668 protected or may not expose installed package metadata:

```bash
python -m venv /tmp/cli-router-release-venv
/tmp/cli-router-release-venv/bin/python -m pip install -e ".[dev]"
/tmp/cli-router-release-venv/bin/python -m pytest tests -q
/tmp/cli-router-release-venv/bin/ruff check cli_router scripts tests
/tmp/cli-router-release-venv/bin/mypy cli_router scripts
/tmp/cli-router-release-venv/bin/coverage run -m pytest tests -q
/tmp/cli-router-release-venv/bin/coverage report
/tmp/cli-router-release-venv/bin/python -m cli_router.cli --help
/tmp/cli-router-release-venv/bin/python -m cli_router.cli check
env GITHUB_REF_NAME=vX.Y.Z /tmp/cli-router-release-venv/bin/python scripts/check_release.py
```

Build into a fresh temporary directory, check both distributions, and install the wheel for a smoke test:

```bash
/tmp/cli-router-release-venv/bin/python -m build --outdir /tmp/cli-router-release-dist
/tmp/cli-router-release-venv/bin/python -m twine check /tmp/cli-router-release-dist/*
```

Inspect filenames, metadata, and archive contents. Ensure the wheel and source distribution both carry the intended version and no unexpected secret or generated runtime artifact.

## 4. Commit, Push, and Merge Through a Pull Request

Stage only the intended release files, commit, and push the release branch:

```bash
git add pyproject.toml cli_router/__init__.py CHANGELOG.md
git commit -m "Release X.Y.Z"
git push -u origin release/X.Y.Z
gh pr create --base main --head release/X.Y.Z --title "Release X.Y.Z" --body-file /tmp/cli-router-release-pr.md
```

Monitor all required checks:

```bash
gh pr checks --watch
```

The expected protection set currently includes five Python-version test jobs and `Quality and package`. Merge only when all are green. The repository normally uses a squash merge:

```bash
gh pr merge --squash --delete-branch
```

If direct push reports `GH006`, do not bypass it; use this pull-request path.

## 5. Synchronize Main and Tag the Merge Commit

After merge:

```bash
git switch main
git pull --ff-only origin main
git show --no-patch --format=fuller HEAD
git tag -a vX.Y.Z -m "Release X.Y.Z"
git rev-parse HEAD
git rev-parse 'vX.Y.Z^{}'
git push origin vX.Y.Z
```

The two `rev-parse` results must match before pushing. This matters because squash merging produces a different commit from the pull-request source commit.

If an unpushed local tag was created too early, recreate it at the merged `main` commit. Never force-update a public release tag without first determining whether a GitHub Release or PyPI publication already used it.

## 6. Publish the GitHub Release

Prepare release notes from the changelog, then publish a non-draft release:

```bash
gh release create vX.Y.Z \
  --verify-tag \
  --title "vX.Y.Z" \
  --notes-file /tmp/cli-router-X.Y.Z-release-notes.md
```

Publishing the GitHub Release triggers `.github/workflows/publish.yml`. No local PyPI credential is needed.

## 7. Monitor Trusted Publishing

Find and watch the release-triggered run:

```bash
gh run list --workflow publish.yml --limit 5
gh run watch RUN_ID --interval 10 --exit-status
```

Require success from:

- Python 3.10-3.14 package tests and release-identity validation.
- Distribution build, Twine checks, and artifact upload.
- The final `Publish CLI-Router to PyPI` job.

If a job fails, inspect it with `gh run view RUN_ID --log-failed`. Do not fall back to a manual upload until the failure is understood and a deliberate recovery decision is made.

## 8. Verify the Public Package

Use a fresh environment and force the public index to prove that the requested version—not a local checkout or cache—is installable:

```bash
python -m venv /tmp/cli-router-pypi-verify
/tmp/cli-router-pypi-verify/bin/python -m pip install \
  --no-cache-dir \
  --index-url https://pypi.org/simple \
  cli-router==X.Y.Z
/tmp/cli-router-pypi-verify/bin/python -c \
  "from importlib.metadata import version; import cli_router; print(version('cli-router')); print(cli_router.__version__)"
/tmp/cli-router-pypi-verify/bin/cli-router check
```

Both printed versions must equal `X.Y.Z`, and the configuration check must succeed.

## 9. Record Completion

Update `STATE.md` with the verified release, tag and main commit, GitHub Release URL, publish workflow URL, public PyPI verification, and any remaining blocker. Add only reusable lessons to `MEMORY.md`.

Finish with:

```bash
git status --short --branch
```

The checkout should be clean and synchronized with `origin/main` after the documentation update is merged.
