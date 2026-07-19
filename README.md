# CLI-Router

CLI-Router is a Python command-line orchestrator for AI coding tools. It routes ordered workflow stages across external CLIs such as Claude Code, Codex, Hermes, Grok, and local model tools.

The router is intentionally programmatic and non-intelligent: it loads configured commands, renders prompt templates, captures stdout/stderr, extracts configured output, writes artifacts such as `PLAN.md`, and records run artifacts.

## Install

```bash
pip install cli-router
```

For CLI usage, `pipx` is recommended:

```bash
pipx install cli-router
```

From a local checkout:

```bash
python -m pip install -e .
```

## Commands

```bash
cli-router --help
cli-router --version
cli-router --version --json
cli-router
cli-router tui
cli-router plan "Add a health check endpoint"
cli-router run "Add a health check endpoint"
cli-router run "Add a health check endpoint" --stages planner,review,coder
cli-router implement
cli-router implement --stages coder,review
cli-router check
cli-router check --json
cli-router config show
cli-router tools list
cli-router tools test claude-planner
cli-router tools test claude-planner --json
cli-router runs
cli-router runs show 2026-07-07T14-22-10
```

Running `cli-router` with no subcommand opens the interactive TUI. `plan` runs the planner stage and writes `PLAN.md`. `run` runs all enabled workflow stages in configured order. `implement` runs enabled post-planner stages using the existing plan file. `--stages` selects specific stage IDs and runs them in the order provided, including stages that are disabled by default.

`runs` lists previous run artifact directories from `defaults.run_dir`, newest first. `runs show <id>` prints the saved manifest summary for one run and lists the artifact files present. `<id>` may be a unique prefix of the timestamp directory name.

`tui` opens a main menu:

- Prompt: enter a prompt and run the enabled workflow.
- Workflow: select, reorder, and run stages.
- Stage configuration: select stages, edit prompts, and choose model configs.
- Model Config: add providers/model configs and edit provider, model, and effort metadata.

On first run, if no config exists, the TUI asks which providers to enable. Built-in choices include `codex`, `claude`, `hermes`, and `grok`. The default workflow uses the first selected provider, and CLI-Router writes the generated config to `~/.cli-router/config.yaml`.

Navigation is consistent across the TUI:

- Up/Down arrows move the cursor.
- Enter opens or runs the selected action.
- `b` goes back to the main menu from submenus.
- `q` quits the TUI.
- Esc cancels the current screen, picker, or text entry.
- Ctrl+C cancels immediately and exits with code `130`.

The workflow screen uses a checkbox selector in the left column and shows each stage prompt:

- Up/Down arrows move the cursor.
- Space toggles a stage between `☑` selected and `☐` skipped.
- `u` and `d` reorder the current stage.
- `i` inserts a stage from the stage library after the current stage.
- `x` removes the current stage from the workflow.
- Enter runs the checked stages in the displayed order.

Stage prompts use these official variables:

- `[user prompt]`: the request entered through the Prompt menu.
- `[previous stage output]`: the final extracted output of the immediately preceding stage (empty for the first stage).
- `[all stage outputs]`: the final outputs of every completed stage so far, each labeled with its stage id.
- `[plan file]`: the path to the workflow plan file (`PLAN.md`).

When stages run, those display variables are converted to CLI-Router's internal placeholders.
In Stage configuration, the stage-prompt editor keeps this variable legend visible,
supports multiline prompts with Enter, and saves with Ctrl+D.

## Configuration

CLI-Router looks for config in this order:

1. `./cli-router.yaml`
2. `./.cli-router.yaml`
3. `~/.cli-router/config.yaml`
4. `~/.config/cli-router/config.yaml`
5. Built-in defaults

The TUI persists first-run setup and TUI edits to `~/.cli-router/config.yaml`. Project-local config files still take precedence when present.

Project configs can require a compatible CLI-Router release with a PEP 440 version range. Every command, including `check`, fails before execution when the running package is outside the range. Use config `version: 2` with this declaration for a hard compatibility boundary: pre-feature routers reject the schema version instead of ignoring an unknown top-level key.

```yaml
version: 2
requires_cli_router: ">=0.3.1,<0.4.0"
```

Minimal example:

```yaml
version: 2
requires_cli_router: ">=0.3.1,<0.4.0"

defaults:
  plan_file: PLAN.md
  run_dir: .cli-router/runs
  stop_on_failure: true
  tui_verbosity: condensed
  log_dir: ~/.cli-router/logs
  log_level: INFO

tools:
  claude-planner:
    type: claude
    timeout_seconds: 60
    command:
      - claude
      - -p
      - --permission-mode
      - plan
      - --output-format
      - json
      - "{prompt}"
    output:
      format: json
      extract: result

  grok-reviewer:
    type: grok
    cwd: "{target_root}"
    environment_mode: allowlist
    environment_allowlist:
      - HOME
      - USER
      - LOGNAME
      - PATH
      - GROK_HOME
    environment:
      TERM: dumb
    environment_unset:
      - GITHUB_TOKEN
      - SSH_AUTH_SOCK
    stdin: closed
    redact_environment_values:
      - GROK_TOKEN
    command:
      - grok
      - --single
      - "{prompt}"
    output:
      format: text

  codex-planner:
    type: codex
    timeout_seconds: 120
    command:
      - codex
      - --ask-for-approval
      - never
      - exec
      - "{prompt}"
    output:
      format: text

  codex-coder:
    type: codex
    timeout_seconds: 120
    command:
      - codex
      - --ask-for-approval
      - never
      - exec
      - "{prompt}"
    output:
      format: text

  grok-coder:
    type: grok
    timeout_seconds: 120
    command:
      - grok
      - --single
      - "{prompt}"
    output:
      format: text

  codex-reviewer:
    type: codex
    timeout_seconds: 120
    command:
      - codex
      - --ask-for-approval
      - never
      - exec
      - "{prompt}"
    output:
      format: text

stage_library:
  - id: coder
    tool: codex-coder
    input_template: |
      Please implement the plan in {plan_path}.

      Original user request:
      {user_prompt}

  - id: qa
    tool: codex-reviewer
    input_template: |
      Review the changes against {plan_path}.

      Original user request:
      {user_prompt}

  - id: summary
    tool: codex-reviewer
    input_template: |
      Summarize the workflow outcome for {user_prompt}.

workflows:
  default:
    stages:
      - id: planner
        tool: claude-planner
        fallback_tools:
          - tool: codex-planner
            on:
              - auth_required
              - usage_limit
              - timeout
              - transport_failure
        max_fallback_attempts: 1
        input_template: |
          You are the planning model for a coding-agent handoff.

          User request:
          {user_prompt}

          Inspect this repository and produce a concrete implementation plan.
          Do not edit files.
          Write the plan in Markdown.
        output_file: PLAN.md

      - id: coder
        tool: codex-coder
        input_template: |
          Please implement the plan in {plan_path}.

          Original user request:
          {user_prompt}

      - id: review
        tool: codex-reviewer
        enabled: false
        input_template: |
          Review the changes against {plan_path}.

          Original user request:
          {user_prompt}
```

Command args, `cwd`, and configured environment values use literal placeholder replacement. Supported runtime placeholders include `{prompt}`, `{user_prompt}`, `{plan_path}`, `{previous_output}`, `{all_stage_outputs}`, and `{target_root}`. `{target_root}` is the directory from which CLI-Router was invoked.

Workflow stages are modular:

- `stages` order is execution order for `cli-router run`.
- `stage_library` is an optional top-level list of reusable stage templates that the TUI can insert into a workflow.
- Inserting a template whose ID already exists auto-suffixes the new workflow stage, for example `coder` becomes `coder-2`, so artifacts remain distinct.
- `enabled: false` keeps a stage out of default `run` and `implement` execution.
- `--stages review,coder` selects stages explicitly and controls their order.
- `cli-router tui` provides an interactive main menu with Prompt first. The workflow screen has checkbox selection, stage ordering, stage-library insertion, selected-stage removal, and a Prompt column that previews each stage prompt.
- The TUI stage configuration screen lets users select a stage with Up/Down, press Enter to edit its prompt, and choose its Model Config from a picker. The stage-prompt editor keeps the official variable legend visible, supports multiline prompts with Enter, and saves with Ctrl+D. `A` adds a custom stage, `I` inserts from the stage library, and `X` removes the selected stage. Changes persist to `~/.cli-router/config.yaml` when the TUI is operating on the generated or user-level config. Project-local configs are left unchanged.
- The TUI calls configured tools "Model Configs" and lets users add providers/model configs or edit provider, model, and effort metadata. Runtime command configuration remains in YAML.
- Model Config rows are edited with `E`; Enter does not start editing. Provider, model, and effort are selected with arrow-key pickers.
- Esc cancels the active TUI screen, picker, or text entry without applying partial changes. Ctrl+C cancels from any TUI screen, picker, or text entry and exits with code `130`.
- When a workflow starts from Prompt or Workflow, the TUI immediately shows a "Running workflow" panel and streams condensed stage progress while external tools execute.
- By default, the TUI collapses verbose thinking blocks and unified diffs, then prints a compact summary with the run artifact directory. Set `defaults.tui_verbosity: full` to restore the raw stdout/stderr dump.
- Model options are discovered through the provider CLI when possible with stdin closed and a short timeout, then fall back to built-in known model names. Codex discovery uses `codex debug models` and reads the returned model catalog, and Grok discovery uses `grok models`. Claude currently uses a static fallback list because Claude Code does not expose an equivalent model catalog command.
- TUI stage prompts use official bracket variables: `[user prompt]`, `[previous stage output]`, `[all stage outputs]`, and `[plan file]`. Unknown bracket variables are rejected.
- A stage with `output_file` writes its extracted output to that file.
- A stage with `updates_plan: true` makes later `{plan_path}` placeholders point to its `output_file`.

When a stage command fails, CLI-Router records stdout/stderr and classifies common failures. Usage-limit messages such as provider quota, rate-limit, or 429 errors are reported as usage-limit failures. Authentication failures such as provider login errors are reported as `auth_required` and include the provider's first error line. Network/connection errors are classified as `transport_failure`. Commands can set `timeout_seconds`; timed-out commands are recorded with exit code `124`.

Fallback is fail-closed and only available for the operational failure kinds `auth_required`, `usage_limit`, `timeout`, and `transport_failure`. A structured fallback selects the allowed kinds with `on`; `max_fallback_attempts` caps how many alternates may run. Legacy string entries remain supported as shorthand for the full safe operational set. CLI-Router never falls back after generic command/semantic failures, unsupported-model configuration, malformed structured output (`extraction_failed`), or runtime configuration errors. Successful extracted verdicts such as `FAIL`, `PASS_WITH_WARNINGS`, or `INCONCLUSIVE` are final stage output and do not trigger fallback.

Each fallback attempt records `primary_tool`, `primary_failure_kind`, `fallback_tool`, `fallback_reason: allowed_failure_kind`, and the one-based `fallback_attempt` in `run.yaml`.

Tool execution inherits the router process environment and stdin by default for backward compatibility. `environment_mode: allowlist` starts from only `environment_allowlist`; `environment` then adds or overrides values, and `environment_unset` removes named values. `stdin: closed` connects the child to the null device. `redact_environment_values` names environment variables whose nonempty values are replaced in captured stdout, stderr, streamed output, and the recorded command before artifacts are written. This is an explicit privacy mode: without it, raw output remains unchanged.

## Machine-readable output

`--json` is supported by `--version`, `check`, `plan`, `run`, `implement`, and `tools test` (place it after the subcommand for command-specific use). Each invocation prints exactly one JSON object with `schema_version: 1`. The stable envelope includes:

- CLI-Router version and command.
- Config source and SHA-256 checksum (source-file bytes, or the canonical built-in config).
- Workflow, run ID/directory, duration, exit code, overall outcome, and error.
- Whether fallback was used and why.
- Per-attempt stage, tool, provider/model, attempt number, exit code, failure kind, duration, fallback provenance, and artifact paths.

Examples:

```bash
cli-router --version --json
cli-router check --json
cli-router tools test grok-reviewer --json
cli-router run "Review this target" --json
cli-router implement --json
```

## Artifacts

Each run writes artifacts under `.cli-router/runs/`:

```text
.cli-router/runs/2026-07-07T14-22-10/
  run.yaml
  planner.stdout
  planner.stderr
  planner.extracted.md
  coder.stdout
  coder.stderr
```

Inspect previous runs from the CLI:

```bash
cli-router runs
cli-router runs list
cli-router runs show 2026-07-07T14-22-10
```

Each run manifest records workflow start/finish timestamps, total duration, per-stage attempt durations, subprocess durations, byte counts, failure kind, and retry count. `cli-router runs show <id>` prints the saved durations.

Diagnostic logs are written to `~/.cli-router/logs/` by default:

- `cli-router.log`: rotating structured text log with workflow and stage lifecycle events.
- `metrics.jsonl`: one JSON object per run for aggregate analysis.

Set `defaults.log_dir` to store these files somewhere else, and `defaults.log_level` to change verbosity.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
ruff check cli_router scripts tests
mypy cli_router scripts
coverage run -m pytest tests -q && coverage report
```

Build and check a release locally:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

The protected-branch, tagging, GitHub Release, trusted PyPI publishing, authentication, and public-package verification procedure is documented in the [release runbook](https://github.com/coolrazor007/cli-router/blob/main/docs/releasing.md).

For cross-agent continuity, repository rules live in [AGENTS.md](https://github.com/coolrazor007/cli-router/blob/main/AGENTS.md), the last verified operational state lives in [STATE.md](https://github.com/coolrazor007/cli-router/blob/main/STATE.md), and durable environment lessons live in [MEMORY.md](https://github.com/coolrazor007/cli-router/blob/main/MEMORY.md). These files are intentionally tool- and vendor-neutral.
