# AGENTS.md

Guidance for AI agents working in this repository.

## Project Purpose

CLI-Router is a Python command-line orchestrator for external AI coding CLIs. It is intentionally programmatic and non-intelligent:

- It does not inspect repositories on its own unless a configured external tool does so.
- It does not semantically choose models or providers.
- It loads YAML config, renders prompt templates, runs configured commands, captures stdout/stderr, extracts configured output, writes artifacts, classifies failures, and applies configured fallback behavior.

Keep that boundary intact. New behavior should make orchestration more reliable or configurable, not turn this package into an agent.

## Source Layout

- `cli_router/cli.py`: argparse CLI entry point and command dispatch.
- `cli_router/config.py`: config discovery, merge, and validation.
- `cli_router/runner.py`: subprocess execution, placeholder rendering, command timeout handling.
- `cli_router/workflows.py`: `plan`, `implement`, and full `run` workflow execution.
- `cli_router/extractors.py`: text and JSON output extraction.
- `cli_router/failures.py`: external-tool failure classification and user-facing messages.
- `cli_router/artifacts.py`: run directory and artifact persistence.
- `cli_router/tools.py`: `tools list` and `tools test`.
- `cli_router/presets/`: packaged YAML defaults/presets.
- `examples/`: user-facing example configs.
- `tests/`: pytest coverage using fake subprocess CLIs, not real Claude/Codex calls.

## Development Commands

Use these from the repository root:

```bash
python -m pytest tests -q
python -m cli_router.cli --help
python -m cli_router.cli check
```

Package verification:

```bash
python -m build
python -m twine check dist/*
```

Editable install may fail on system Python environments protected by PEP 668. Use a virtual environment:

```bash
python -m venv /tmp/cli-router-venv
/tmp/cli-router-venv/bin/python -m pip install -e .
/tmp/cli-router-venv/bin/cli-router --help
```

`python -m build` and package install may need network access to fetch build dependencies.

## Configuration Semantics

Config lookup order is:

1. `./cli-router.yaml`
2. `./.cli-router.yaml`
3. `~/.config/cli-router/config.yaml`
4. Built-in defaults from `cli_router/presets/generic.yaml`

Config version is currently `version: 1`.

Supported placeholders are literal token replacements:

- `{prompt}`
- `{user_prompt}`
- `{plan_path}`

Do not switch to Python `str.format` semantics without a strong reason. Current literal replacement intentionally allows command snippets and prompts to contain unrelated braces.

Workflow stages use:

- `tool`: primary tool name.
- `fallback_tools`: optional ordered list of tool names to try after failed attempts.
- `input_template`: rendered prompt sent as `{prompt}` to the tool command.
- `output_file`: planner output path, usually `PLAN.md`.

Tool config supports:

- `command`: list-form command preferred; string-form is parsed with `shlex.split`.
- `output.format`: `text` or `json`.
- `output.extract`: top-level or dotted JSON field path.
- `timeout_seconds`: optional positive number; timeout returns exit code `124`.

## Runtime Artifacts

Runs write to `.cli-router/runs/<timestamp>/`.

Primary stage attempt artifacts use the stage id:

- `planner.stdout`
- `planner.stderr`
- `planner.extracted.md`

Fallback attempts include the tool name:

- `planner.codex-planner.stdout`
- `planner.codex-planner.stderr`
- `planner.codex-planner.extracted.md`

Every run writes `run.yaml` containing stage summaries, commands, return codes, extracted output, and `failure_kind`.

`.cli-router/runs/` is ignored by git. Do not commit generated run artifacts.

## Failure Behavior

Failure classification lives in `cli_router/failures.py`.

Current `failure_kind` values include:

- `usage_limit`
- `unsupported_model`
- `timeout`
- `command_not_found`
- `command_failed`
- `extraction_failed`

Known real-world messages are covered by tests, including:

- Claude: `You've hit your session limit ...`
- Claude/rate/quota variants containing usage limit, session limit, rate limit, quota, 429, or too many requests.
- Codex/OpenAI unsupported model messages such as `model is not supported`.

When adding a new failure classifier, add a focused test in `tests/test_failures.py` using the real observed stdout/stderr text when possible.

## Testing Rules

Prefer tests that use temporary configs and fake commands such as:

```python
[sys.executable, "-c", "import json; print(json.dumps({'result': 'plan'}))"]
```

Do not require real Claude, Codex, Hermes, OpenAI, network access, or credentials in the normal test suite.

Use `tmp_path` and `monkeypatch.chdir(tmp_path)` for workflow tests that write `PLAN.md` or `.cli-router/runs/`.

When changing workflow behavior, cover:

- plan-only execution
- full planner-to-coder execution
- missing `PLAN.md` for `implement`
- nonzero command failures
- fallback ordering and artifact names
- failure classification and exit codes

When changing packaging, verify:

```bash
python -m pytest tests -q
python -m build
python -m twine check dist/*
```

## Real External CLI Trials

Real Claude/Codex trials are useful but should be explicit and isolated.

Use a temporary directory such as `/tmp/cli-router-real-test`; do not run real provider trials in the repo root unless the user explicitly wants repo files changed.

Recommended safety controls:

- Add `timeout_seconds` to every real tool.
- For Codex trials, prefer `codex --ask-for-approval never exec --skip-git-repo-check --ephemeral --sandbox read-only ...`.
- Use harmless prompts and include "Do not edit files" in `input_template`.
- Expect provider/model availability to vary by account; classify and report unsupported model failures instead of treating them as code defects.

Known observed Codex behavior in this environment:

- Local Codex config default model was `gpt-5.5`.
- `gpt-5` and `gpt-5-codex` returned unsupported-model errors for the ChatGPT-backed account.
- `gpt-5.5` succeeded for both planner and coder in the real fallback trial.

These observations are environment-specific. Do not hardcode them into package defaults without a product decision.

## Design Constraints

- Keep the CLI stable: `cli-router run`, `plan`, `implement`, `check`, `config show`, `tools list`, `tools test <name>`.
- Keep dependencies minimal. Current runtime dependencies are `pyyaml` and `rich`; the CLI uses `argparse`.
- Prefer list-form subprocess commands in examples and tests.
- Preserve stdout/stderr exactly in artifacts. Do not redact or transform raw logs unless a user explicitly requests a privacy feature.
- Avoid hidden behavior. If CLI-Router retries or falls back, make that visible through summaries and `run.yaml`.
- Do not make built-in presets depend on provider credentials or paid services for basic `cli-router check`.
- Do not mutate user configuration unless implementing an explicit config-editing command.

## Git Hygiene

The worktree may contain user or generated changes. Do not revert files you did not intentionally change.

Generated files that should normally remain untracked:

- `.cli-router/runs/`
- `dist/`
- `build/`
- `*.egg-info/`
- `__pycache__/`
- `.pytest_cache/`

Before finalizing substantive changes, run `git status --short` and report any verification commands run.

