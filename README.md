# CLI-Router

CLI-Router is a Python command-line orchestrator for AI coding tools. It routes ordered workflow stages across external CLIs such as Claude Code, Codex, Hermes, and local model tools.

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
cli-router
cli-router tui
cli-router plan "Add a health check endpoint"
cli-router run "Add a health check endpoint"
cli-router run "Add a health check endpoint" --stages planner,review,coder
cli-router implement
cli-router implement --stages coder,review
cli-router check
cli-router config show
cli-router tools list
cli-router tools test claude-planner
cli-router runs
cli-router runs show 2026-07-07T14-22-10
```

Running `cli-router` with no subcommand opens the interactive TUI. `plan` runs the planner stage and writes `PLAN.md`. `run` runs all enabled workflow stages in configured order. `implement` runs enabled post-planner stages using the existing plan file. `--stages` selects specific stage IDs and runs them in the order provided, including stages that are disabled by default.

`runs` lists previous run artifact directories from `defaults.run_dir`, newest first. `runs show <id>` prints the saved manifest summary for one run and lists the artifact files present. `<id>` may be a unique prefix of the timestamp directory name.

`tui` opens a main menu:

- Prompt: enter a prompt and run the enabled workflow.
- Workflow: select, reorder, and run stages.
- Stage configuration: select stages, edit prompts, and choose model configs.
- Model Config: edit provider, model, and effort metadata.

On first run, if no config exists, the TUI asks which providers to enable. Built-in choices include `codex`, `claude`, and `hermes`. The default workflow uses the first selected provider, and CLI-Router writes the generated config to `~/.cli-router/config.yaml`.

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
- Enter runs the checked stages in the displayed order.

Stage prompts use these official variables:

- `[user prompt]`: the request entered through the Prompt menu.
- `[previous stage output]`: the output file from the previous planning/output stage.

When stages run, those display variables are converted to CLI-Router's internal placeholders.

## Configuration

CLI-Router looks for config in this order:

1. `./cli-router.yaml`
2. `./.cli-router.yaml`
3. `~/.cli-router/config.yaml`
4. `~/.config/cli-router/config.yaml`
5. Built-in defaults

The TUI persists first-run setup and TUI edits to `~/.cli-router/config.yaml`. Project-local config files still take precedence when present.

Minimal example:

```yaml
version: 1

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

workflows:
  default:
    stages:
      - id: planner
        tool: claude-planner
        fallback_tools:
          - codex-planner
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

Command args and templates support `{prompt}`, `{user_prompt}`, and `{plan_path}` placeholders.

Workflow stages are modular:

- `stages` order is execution order for `cli-router run`.
- `enabled: false` keeps a stage out of default `run` and `implement` execution.
- `--stages review,coder` selects stages explicitly and controls their order.
- `cli-router tui` provides an interactive main menu with Prompt first. The workflow screen has checkbox selection, stage ordering, and a Prompt column that previews each stage prompt.
- The TUI stage configuration screen lets users select a stage with Up/Down, press Enter to edit its prompt, and choose its Model Config from a picker. `A` adds a stage and also uses the Model Config picker. Changes persist to `~/.cli-router/config.yaml` when the TUI is operating on the generated or user-level config. Project-local configs are left unchanged.
- The TUI calls configured tools "Model Configs" and lets users edit provider, model, and effort metadata. Runtime command configuration remains in YAML.
- Model Config rows are edited with `E`; Enter does not start editing. Provider, model, and effort are selected with arrow-key pickers.
- Esc cancels the active TUI screen, picker, or text entry without applying partial changes. Ctrl+C cancels from any TUI screen, picker, or text entry and exits with code `130`.
- When a workflow starts from Prompt or Workflow, the TUI immediately shows a "Running workflow" panel and streams condensed stage progress while external tools execute.
- By default, the TUI collapses verbose thinking blocks and unified diffs, then prints a compact summary with the run artifact directory. Set `defaults.tui_verbosity: full` to restore the raw stdout/stderr dump.
- Model options are discovered through the provider CLI when possible with stdin closed and a short timeout, then fall back to built-in known model names. Codex discovery uses `codex debug models` and reads the returned model catalog, so newly available Codex models appear without a package update. Claude currently uses a static fallback list because Claude Code does not expose an equivalent model catalog command.
- TUI stage prompts use official bracket variables: `[user prompt]` and `[previous stage output]`. Unknown bracket variables are rejected.
- A stage with `output_file` writes its extracted output to that file.
- A stage with `updates_plan: true` makes later `{plan_path}` placeholders point to its `output_file`.

When a stage command fails, CLI-Router records stdout/stderr and classifies common failures. Usage-limit messages such as provider quota, rate-limit, or 429 errors are reported as usage-limit failures. Authentication failures such as provider login errors are reported as `auth_required` and include the provider's first error line. Commands can set `timeout_seconds`; timed-out commands are recorded with exit code `124`. A stage can define `fallback_tools` to try alternate configured tools in order after a failed primary tool.

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
python -m pip install -e .
python -m pytest
```

Build and check a release locally:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```
