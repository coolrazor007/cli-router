# CLI-Router

CLI-Router is a Python command-line orchestrator for AI coding tools. It routes planning and coding stages across external CLIs such as Claude Code, Codex, Hermes, and local model tools.

The router is intentionally programmatic and non-intelligent: it loads configured commands, renders prompt templates, captures stdout/stderr, extracts planner output, writes `PLAN.md`, and records run artifacts.

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
cli-router plan "Add a health check endpoint"
cli-router run "Add a health check endpoint"
cli-router implement
cli-router check
cli-router config show
cli-router tools list
cli-router tools test claude-planner
```

`plan` runs the planner stage and writes `PLAN.md`. `run` runs planner then coder. `implement` runs the coder stage using the existing plan file.

## Configuration

CLI-Router looks for config in this order:

1. `./cli-router.yaml`
2. `./.cli-router.yaml`
3. `~/.config/cli-router/config.yaml`
4. Built-in defaults

Minimal example:

```yaml
version: 1

defaults:
  plan_file: PLAN.md
  run_dir: .cli-router/runs
  stop_on_failure: true

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
```

Command args and templates support `{prompt}`, `{user_prompt}`, and `{plan_path}` placeholders.

When a stage command fails, CLI-Router records stdout/stderr and classifies common failures. Usage-limit messages such as provider quota, rate-limit, or 429 errors are reported as usage-limit failures. Commands can set `timeout_seconds`; timed-out commands are recorded with exit code `124`. A stage can define `fallback_tools` to try alternate configured tools in order after a failed primary tool.

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
