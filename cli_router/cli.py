"""Command-line interface for CLI-Router."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .config import ConfigError, config_to_yaml, load_config
from .runs import RunDetail, RunInfo, list_runs, show_run
from .tools import list_tools, test_tool
from .tui import run_tui
from .workflows import WorkflowSummary, implement_workflow, plan_workflow, run_workflow


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        config = load_config(args.config)
        if args.command is None:
            return run_tui(config)
        if args.command == "plan":
            return _print_summary(plan_workflow(config, args.prompt, args.workflow, _parse_stage_names(args.stages)))
        if args.command == "run":
            return _print_summary(run_workflow(config, args.prompt, args.workflow, _parse_stage_names(args.stages)))
        if args.command == "implement":
            return _print_summary(implement_workflow(config, args.workflow, _parse_stage_names(args.stages)))
        if args.command == "tui":
            return run_tui(config, args.workflow, args.prompt)
        if args.command == "check":
            print("Configuration OK")
            return 0
        if args.command == "config" and args.config_command == "show":
            print(config_to_yaml(config), end="")
            return 0
        if args.command == "tools" and args.tools_command == "list":
            for name in list_tools(config):
                print(name)
            return 0
        if args.command == "tools" and args.tools_command == "test":
            summary = test_tool(config, args.name)
            print(f"{args.name}: exit {summary.result.returncode}")
            print(f"run_dir: {summary.run_dir}")
            return summary.result.returncode
        if args.command == "runs" and args.runs_command in (None, "list"):
            _print_runs_list(list_runs(config))
            return 0
        if args.command == "runs" and args.runs_command == "show":
            _print_run_detail(show_run(config, args.id))
            return 0
    except (ConfigError, KeyError) as exc:
        print(f"cli-router: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli-router", description="Route planning and coding stages across external CLIs.")
    parser.add_argument("--config", help="Path to a cli-router YAML config file.")

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run enabled workflow stages.")
    run_parser.add_argument("prompt")
    run_parser.add_argument("--workflow", default="default")
    run_parser.add_argument("--stages", help="Comma-separated stage IDs to run in the given order.")

    plan_parser = subparsers.add_parser("plan", help="Run only the planner stage.")
    plan_parser.add_argument("prompt")
    plan_parser.add_argument("--workflow", default="default")
    plan_parser.add_argument("--stages", help="Comma-separated stage IDs to run in the given order.")

    implement_parser = subparsers.add_parser("implement", help="Run enabled post-planner stages using the plan file.")
    implement_parser.add_argument("--workflow", default="default")
    implement_parser.add_argument("--stages", help="Comma-separated stage IDs to run in the given order.")

    tui_parser = subparsers.add_parser("tui", help="Open an interactive stage selector.")
    tui_parser.add_argument("prompt", nargs="?")
    tui_parser.add_argument("--workflow", default="default")

    subparsers.add_parser("check", help="Validate the loaded configuration.")

    config_parser = subparsers.add_parser("config", help="Inspect configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("show", help="Print the loaded configuration.")

    tools_parser = subparsers.add_parser("tools", help="Inspect configured tools.")
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command")
    tools_subparsers.add_parser("list", help="List configured tools.")
    test_parser = tools_subparsers.add_parser("test", help="Run a configured tool with a test prompt.")
    test_parser.add_argument("name")

    runs_parser = subparsers.add_parser("runs", help="Inspect previous run artifacts.")
    runs_subparsers = runs_parser.add_subparsers(dest="runs_command")
    runs_subparsers.add_parser("list", help="List previous runs.")
    show_parser = runs_subparsers.add_parser("show", help="Show details for one run.")
    show_parser.add_argument("id")

    return parser


def _parse_stage_names(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [stage_name.strip() for stage_name in value.split(",") if stage_name.strip()]


def _print_summary(summary: WorkflowSummary) -> int:
    print(f"run_dir: {summary.run_dir}")
    print(f"plan_path: {summary.plan_path}")
    for stage in summary.stages:
        print(f"{stage.stage_id}: exit {stage.result.returncode}")
    if summary.error:
        print(f"error: {summary.error}", file=sys.stderr)
    print(f"exit_code: {summary.exit_code}")
    return summary.exit_code


def _print_runs_list(runs: list[RunInfo]) -> None:
    if not runs:
        print("No runs found.")
        return

    print("id\tworkflow\texit\tprompt")
    for run in runs:
        exit_code = "unknown" if run.exit_code is None else str(run.exit_code)
        prompt = _truncate(run.user_prompt.replace("\n", " "), 60)
        suffix = f" ({run.error})" if run.error else ""
        print(f"{run.id}\t{run.workflow}\t{exit_code}\t{prompt}{suffix}")


def _print_run_detail(detail: RunDetail) -> None:
    manifest = detail.manifest
    print(f"run: {detail.id}")
    print(f"path: {detail.path}")
    print(f"workflow: {manifest.get('workflow', manifest.get('command', 'unknown'))}")
    print(f"exit_code: {manifest.get('exit_code', 'unknown')}")
    duration = manifest.get("duration_seconds")
    if duration is None and isinstance(manifest.get("metrics"), dict):
        duration = manifest["metrics"].get("total_duration_seconds")
    if duration is not None:
        print(f"duration_seconds: {duration}")
    if manifest.get("error"):
        print(f"error: {manifest['error']}")

    stages = manifest.get("stages", [])
    if isinstance(stages, list) and stages:
        print("stages:")
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            stage_id = stage.get("stage_id", "unknown")
            tool = stage.get("tool", "unknown")
            exit_code = stage.get("exit_code")
            if exit_code is None:
                # Backward compatibility with manifests that embedded the full result.
                result = stage.get("result", {})
                exit_code = result.get("returncode", "unknown") if isinstance(result, dict) else "unknown"
            failure = stage.get("failure_kind") or "none"
            duration = stage.get("duration_seconds")
            duration_text = f", duration {duration}s" if duration is not None else ""
            print(f"  {stage_id}: tool {tool}, exit {exit_code}, failure {failure}{duration_text}")

    print("artifacts:")
    for artifact in detail.artifacts:
        print(f"  {artifact}")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
