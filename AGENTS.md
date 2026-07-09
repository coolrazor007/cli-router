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
- `cli_router/workflows.py`: modular stage execution for `plan`, `implement`, and full `run` workflows.
- `cli_router/tui.py`: Rich-based interactive stage selection and ordering UI.
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
3. `~/.cli-router/config.yaml`
4. `~/.config/cli-router/config.yaml`
5. Built-in defaults from `cli_router/presets/generic.yaml`

Config version is currently `version: 1`.

Supported placeholders are literal token replacements:

- `{prompt}`
- `{user_prompt}`
- `{plan_path}`

Do not switch to Python `str.format` semantics without a strong reason. Current literal replacement intentionally allows command snippets and prompts to contain unrelated braces.

Workflow stages use:

- `id`: unique stage id within the workflow. The list order is the default execution order.
- `tool`: primary tool name.
- `fallback_tools`: optional ordered list of tool names to try after failed attempts.
- `enabled`: optional boolean; `false` skips the stage in default `run` and `implement`, while explicit `--stages` can still select it.
- `input_template`: rendered prompt sent as `{prompt}` to the tool command.
- `output_file`: planner output path, usually `PLAN.md`.
- `updates_plan`: optional boolean; when true, later `{plan_path}` placeholders use this stage's `output_file`.

CLI stage selection:

- `cli-router run` executes all enabled workflow stages in configured order.
- `cli-router plan` defaults to the `planner` stage.
- `cli-router implement` defaults to enabled stages after `planner` and requires the plan file to exist.
- `--stages stage-a,stage-b` on `run`, `plan`, or `implement` selects stages explicitly and runs them in the provided order, including stages with `enabled: false`.
- Bare `cli-router` opens the interactive TUI by default.
- `cli-router tui [prompt]` opens the same interactive main menu. Prompt is the first top-level menu item and should ask for a user prompt, then run the enabled workflow. The Workflow screen uses Unicode checkbox selectors (`☑` selected, `☐` skipped), shows a Prompt column for stage prompt previews, supports `u`/`d` reordering, `b` returns to the main menu, and Enter runs checked stages in displayed order.
- The Stage configuration screen should use row selection, not typed stage IDs, for editing existing stages. It may add stages with a name, model config, and prompt. Persist changes only when the TUI is operating on the generated or user-level `~/.cli-router/config.yaml`; do not silently rewrite project-local configs.
- TUI-authored stage prompts use official bracket variables: `[user prompt]` maps to `{user_prompt}` and `[previous stage output]` maps to `{plan_path}`. Unknown bracket variables should be rejected rather than passed through.

Tool config supports:

- `command`: list-form command preferred; string-form is parsed with `shlex.split`.
- `output.format`: `text` or `json`.
- `output.extract`: top-level or dotted JSON field path.
- `timeout_seconds`: optional positive number; timeout returns exit code `124`.
- `provider`, `model`, and `effort`: optional TUI metadata for Model Config display/editing. These do not replace `command`.

Diagnostic defaults support:

- `defaults.log_dir`: persistent diagnostic log directory, defaulting to `~/.cli-router/logs`.
- `defaults.log_level`: Python logging level for the `cli_router` logger, defaulting to `INFO`.

TUI design rules learned in-session:

- Use consistent navigation everywhere: Up/Down move, Enter opens/runs, `B` goes back from submenus, Esc cancels the active screen/picker/text entry, `Q` quits, and Ctrl+C cancels immediately with exit code `130`. Do not use `Q` as back in one screen and quit in another.
- The main menu order starts with `Prompt`, then `Workflow`, then `Stage configuration`, then `Model Config`.
- Do not show a prompt footer on unrelated menu pages. Prompt entry belongs in the Prompt menu and should kick off the enabled workflow.
- Prompt and Workflow execution should render immediate "Running workflow" feedback before invoking external tools, then stream condensed stage progress.
- TUI run summaries default to condensed output. Thinking blocks and unified diffs should be collapsed in the live view, raw stdout/stderr should remain preserved in run artifacts, and `defaults.tui_verbosity: full` should opt back into the raw dump.
- In the TUI, call configured tools "Model Configs" when assigning them to stages. Stage configuration should show/link `Model Config`, not raw `Tool`.
- Stage Configuration should let users select stages with Up/Down and press Enter to edit the selected stage. Model Config assignment should use a picker of enabled model configs, not typed names.
- Model Config screens should show/edit `Provider`, `Model`, and `Effort`; do not expose raw command strings in that screen.
- Model Config rows should only enter edit mode on `E`, not Enter. Enter should be reserved for selecting inside picker submenus.
- First run should ask users to select providers from at least `codex`, `claude`, and `hermes`, then persist generated config to `~/.cli-router/config.yaml`.
- Persist TUI changes to `~/.cli-router/config.yaml` only when operating on the user config or generated first-run config. Do not silently rewrite project-local configs unless an explicit config-editing command is added.
- Model choices should be discovered through provider CLIs when possible, with stdin closed, a short timeout, and built-in fallbacks for unavailable or interactive CLIs. Codex discovery should use `codex debug models` and parse the returned model catalog; do not rely on static Codex model names except as a fallback. Claude currently uses static current Claude Code model IDs because Claude Code does not expose an equivalent catalog command.
- TUI-authored prompts must use the official bracket variables `[user prompt]` and `[previous stage output]`, which map to `{user_prompt}` and `{plan_path}` internally.
- Use explicit key help such as "Press A to add a stage" rather than terse footer text like "a add stage".

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

Every run writes `run.yaml` containing stage summaries, commands, return codes, extracted output, `failure_kind`, workflow start/finish timestamps, total duration, and per-stage duration metrics.

Persistent diagnostics write under `~/.cli-router/logs/` by default:

- `cli-router.log`: rotating structured text log with workflow and stage lifecycle events.
- `metrics.jsonl`: one JSON object per run with aggregate metrics such as total duration, stage count, retry count, and per-stage subprocess duration/stdout/stderr byte counts.

`.cli-router/runs/` is ignored by git. Do not commit generated run artifacts.

## Failure Behavior

Failure classification lives in `cli_router/failures.py`.

Current `failure_kind` values include:

- `auth_required`
- `usage_limit`
- `unsupported_model`
- `timeout`
- `command_not_found`
- `command_failed`
- `extraction_failed`

Known real-world messages are covered by tests, including:

- Claude: `You've hit your session limit ...`
- Claude: `Not logged in · Please run /login`
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
- full ordered multi-stage execution
- missing `PLAN.md` for `implement`
- nonzero command failures
- fallback ordering and artifact names
- disabled stages and explicit `--stages` ordering
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
- `~/.cli-router/config.yaml` is user state, not repo state.
- `~/.cli-router/logs/` is user diagnostic state, not repo state.
- `dist/`
- `build/`
- `*.egg-info/`
- `__pycache__/`
- `.pytest_cache/`

Before finalizing substantive changes, run `git status --short` and report any verification commands run.
