"""Command-line interface for CLI-Router."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .config import ConfigError, config_to_yaml, load_config
from .tools import list_tools, test_tool
from .workflows import WorkflowSummary, implement_workflow, plan_workflow, run_workflow


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        config = load_config(args.config)
        if args.command == "plan":
            return _print_summary(plan_workflow(config, args.prompt, args.workflow))
        if args.command == "run":
            return _print_summary(run_workflow(config, args.prompt, args.workflow))
        if args.command == "implement":
            return _print_summary(implement_workflow(config, args.workflow))
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
    except (ConfigError, KeyError) as exc:
        print(f"cli-router: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli-router", description="Route planning and coding stages across external CLIs.")
    parser.add_argument("--config", help="Path to a cli-router YAML config file.")

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run planner and coder stages.")
    run_parser.add_argument("prompt")
    run_parser.add_argument("--workflow", default="default")

    plan_parser = subparsers.add_parser("plan", help="Run only the planner stage.")
    plan_parser.add_argument("prompt")
    plan_parser.add_argument("--workflow", default="default")

    implement_parser = subparsers.add_parser("implement", help="Run only the coder stage using the plan file.")
    implement_parser.add_argument("--workflow", default="default")

    subparsers.add_parser("check", help="Validate the loaded configuration.")

    config_parser = subparsers.add_parser("config", help="Inspect configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("show", help="Print the loaded configuration.")

    tools_parser = subparsers.add_parser("tools", help="Inspect configured tools.")
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command")
    tools_subparsers.add_parser("list", help="List configured tools.")
    test_parser = tools_subparsers.add_parser("test", help="Run a configured tool with a test prompt.")
    test_parser.add_argument("name")

    return parser


def _print_summary(summary: WorkflowSummary) -> int:
    print(f"run_dir: {summary.run_dir}")
    print(f"plan_path: {summary.plan_path}")
    for stage in summary.stages:
        print(f"{stage.stage_id}: exit {stage.result.returncode}")
    if summary.error:
        print(f"error: {summary.error}", file=sys.stderr)
    print(f"exit_code: {summary.exit_code}")
    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
