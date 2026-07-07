# PRD Update: CLI-Router Naming and PyPI Packaging

## Project Name

The project is named:

```text
CLI-Router
```

## Package Naming

The project should use separate names for the repository, PyPI package, Python module, and installed command.

```text
GitHub repo name: cli-router
PyPI package name: cli-router
Python module name: cli_router
CLI command name: cli-router
```

This allows the project to be published to PyPI while still following Python import naming conventions.

## Updated Project Summary

CLI-Router is a Python command-line tool that orchestrates external AI coding CLIs.

It accepts a user prompt, sends that prompt to a configured planner CLI, captures the planner output from stdout or structured JSON, writes the extracted result to `PLAN.md`, and then delegates the implementation stage to a configured coder CLI.

The router is intentionally programmatic and non-intelligent. It does not decide what the task means, inspect the codebase on its own, or perform semantic model selection. It simply runs configured commands, captures outputs, writes artifacts, applies failure detection, and executes fallback behavior.

## Primary Command

The installed CLI command should be:

```bash
cli-router
```

Example usage:

```bash
cli-router run "Add a health check endpoint"
```

Other commands:

```bash
cli-router plan "Add a health check endpoint"
cli-router implement
cli-router check
cli-router config show
cli-router tools list
cli-router tools test claude-planner
```

## Python Package Requirements

CLI-Router should be written in Python and packaged for PyPI.

Recommended Python version:

```text
Python >= 3.10
```

Recommended dependency strategy:

```text
MVP:
- pyyaml
- rich

Optional:
- typer
```

The MVP can use `argparse` from the Python standard library to reduce dependencies.

## Recommended Repository Structure

```text
cli-router/
  pyproject.toml
  README.md
  LICENSE
  CHANGELOG.md
  cli_router/
    __init__.py
    cli.py
    config.py
    runner.py
    tools.py
    workflows.py
    extractors.py
    failures.py
    artifacts.py
    presets/
      __init__.py
      claude.yaml
      codex.yaml
      hermes.yaml
      generic.yaml
  tests/
    test_config.py
    test_extractors.py
    test_runner.py
    test_failures.py
  examples/
    cli-router.yaml
    claude_codex.yaml
    local_models.yaml
  .github/
    workflows/
      test.yml
      publish.yml
```

## `pyproject.toml`

CLI-Router should use a modern `pyproject.toml`-based Python package layout. The Python Packaging User Guide recommends `pyproject.toml` for declaring build-system metadata and project configuration.

Recommended initial `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "cli-router"
version = "0.1.0"
description = "A simple Python CLI router for AI planning and coding agents."
readme = "README.md"
requires-python = ">=3.10"
license = "MIT"
authors = [
  { name = "Your Name" }
]
keywords = [
  "ai",
  "cli",
  "claude",
  "codex",
  "agents",
  "llm",
  "router",
  "coding-agent"
]
classifiers = [
  "Development Status :: 3 - Alpha",
  "Environment :: Console",
  "Intended Audience :: Developers",
  "License :: OSI Approved :: MIT License",
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3.10",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
  "Topic :: Software Development",
  "Topic :: Utilities"
]
dependencies = [
  "pyyaml>=6.0",
  "rich>=13.0"
]

[project.urls]
Homepage = "https://github.com/yourname/cli-router"
Repository = "https://github.com/yourname/cli-router"
Issues = "https://github.com/yourname/cli-router/issues"

[project.scripts]
cli-router = "cli_router.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["cli_router"]
```

## Entry Point

The installed console command should be exposed through:

```toml
[project.scripts]
cli-router = "cli_router.cli:main"
```

This allows users to run:

```bash
cli-router run "Fix the failing tests"
```

after installing from PyPI.

## PyPI Build and Upload

For local release testing, CLI-Router should support the standard Python packaging flow:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
python -m twine upload dist/*
```

The Python Packaging User Guide describes building distributions and uploading them to PyPI as the standard packaging workflow.

## Recommended Publishing Strategy

For GitHub releases, CLI-Router should eventually use **PyPI Trusted Publishing** instead of storing a long-lived PyPI API token in GitHub Actions.

PyPI Trusted Publishing uses OpenID Connect so release automation can publish packages without long-lived usernames, passwords, or API tokens.

Recommended future GitHub Actions release flow:

```yaml
name: Publish to PyPI

on:
  release:
    types: [published]

jobs:
  pypi-publish:
    name: Publish CLI-Router to PyPI
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
      contents: read

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install build
        run: python -m pip install --upgrade build

      - name: Build package
        run: python -m build

      - name: Publish package
        uses: pypa/gh-action-pypi-publish@release/v1
```

## Updated Install Targets

CLI-Router should support:

```bash
pip install cli-router
```

and ideally:

```bash
pipx install cli-router
```

`pipx` is especially appropriate because CLI-Router is a command-line application rather than a library-first package.

## Updated Config File Names

CLI-Router should look for config in this order:

```text
./cli-router.yaml
./.cli-router.yaml
~/.config/cli-router/config.yaml
built-in defaults
```

Example:

```yaml
version: 1

defaults:
  plan_file: PLAN.md
  run_dir: .cli-router/runs
  stop_on_failure: true

tools:
  claude-planner:
    type: claude
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
    command:
      - codex
      - exec
      - "{prompt}"
    output:
      format: text

workflows:
  default:
    stages:
      - id: planner
        tool: claude-planner
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

          Rules:
          - Read the plan first.
          - Follow it closely.
          - Make the smallest safe changes.
          - Run relevant tests.
          - Report any deviations.
```

## Updated Artifact Directory

CLI-Router should save run artifacts under:

```text
.cli-router/runs/
```

Example:

```text
.cli-router/runs/2026-07-07T14-22-10/
  run.yaml
  planner.stdout
  planner.stderr
  planner.extracted.md
  coder.stdout
  coder.stderr
```

## Updated MVP Acceptance Criteria

### Package Install

Given the package is published to PyPI, a user should be able to run:

```bash
pipx install cli-router
```

Then:

```bash
cli-router --help
```

The command should display the CLI-Router help output.

### Plan Command

Given:

```bash
cli-router plan "Add logging to the API"
```

CLI-Router should:

* Load config.
* Run the configured planner command.
* Capture stdout and stderr.
* Extract the planner result.
* Write `PLAN.md`.
* Save raw logs to `.cli-router/runs/...`.
* Exit successfully if the planner succeeds.

### Full Run Command

Given:

```bash
cli-router run "Add logging to the API"
```

CLI-Router should:

* Generate `PLAN.md`.
* Run the configured coder command.
* Save planner and coder outputs.
* Print a concise run summary.
* Return a useful process exit code.

### Configurable Tooling

Users should be able to configure Claude, Codex, Hermes, local model CLIs, or any generic command through YAML without modifying Python code.

## Updated Short Description

CLI-Router is a Python command-line orchestrator for AI coding tools. It routes planning, coding, and future review stages across external CLIs such as Claude Code, Codex, Hermes, and local model tools. CLI-Router captures planner output, writes `PLAN.md`, delegates implementation to a coder CLI, records run artifacts, and supports configurable fallback behavior.
