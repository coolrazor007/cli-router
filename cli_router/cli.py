"""Command-line interface for CLI-Router."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import ConfigError, RouterConfig, config_candidates, config_to_yaml, load_config
from .doctor import ProviderHealth, RepairResult, diagnose, repair
from .modelcache import ModelCache
from .receipts import (
    check_receipt,
    error_receipt,
    print_json_receipt,
    tool_test_receipt,
    version_receipt,
    workflow_receipt,
)
from .runs import RunDetail, RunInfo, list_runs, show_run
from .streamfmt import condense_extracted
from .tools import list_tools, test_tool
from .tui import run_tui
from .workflows import WorkflowSummary, implement_workflow, plan_workflow, run_workflow


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1

    if args.version:
        if args.json:
            print_json_receipt(version_receipt())
        else:
            print(f"cli-router {__version__}")
        return 0

    try:
        config = load_config(args.config)
        if args.command is None:
            return run_tui(config)
        if args.command == "plan":
            summary = plan_workflow(config, args.prompt, args.workflow, _parse_stage_names(args.stages))
            return _emit_workflow_summary(config, "plan", args.workflow, summary, args.json)
        if args.command == "run":
            summary = run_workflow(config, args.prompt, args.workflow, _parse_stage_names(args.stages))
            return _emit_workflow_summary(config, "run", args.workflow, summary, args.json)
        if args.command == "implement":
            summary = implement_workflow(config, args.workflow, _parse_stage_names(args.stages))
            return _emit_workflow_summary(config, "implement", args.workflow, summary, args.json)
        if args.command == "tui":
            return run_tui(config, args.workflow, args.prompt)
        if args.command == "check":
            if args.json:
                print_json_receipt(check_receipt(config))
            else:
                print("Configuration OK")
            return 0
        if args.command == "doctor":
            return _run_doctor(repair_enabled=args.repair)
        if args.command == "config" and args.config_command == "show":
            print(config_to_yaml(config), end="")
            return 0
        if args.command == "tools" and args.tools_command == "list":
            for name in list_tools(config):
                print(name)
            return 0
        if args.command == "tools" and args.tools_command == "test":
            tool_summary = test_tool(config, args.name)
            if args.json:
                print_json_receipt(tool_test_receipt(config, args.name, tool_summary))
            else:
                print(f"{args.name}: exit {tool_summary.result.returncode}")
                print(f"run_dir: {tool_summary.run_dir}")
            return tool_summary.result.returncode
        if args.command == "runs" and args.runs_command in (None, "list"):
            _print_runs_list(list_runs(config))
            return 0
        if args.command == "runs" and args.runs_command == "show":
            _print_run_detail(show_run(config, args.id))
            return 0
    except (ConfigError, KeyError) as exc:
        if args.json:
            print_json_receipt(error_receipt(_command_name(args), str(exc), _error_config_path(args.config)))
        else:
            print(f"cli-router: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli-router", description="Route planning and coding stages across external CLIs.")
    parser.add_argument("--config", help="Path to a cli-router YAML config file.")
    parser.add_argument("--version", action="store_true", help="Print the CLI-Router version and exit.")
    parser.add_argument("--json", action="store_true", help="Emit stable machine-readable JSON where supported.")

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run enabled workflow stages.")
    run_parser.add_argument("prompt")
    run_parser.add_argument("--workflow", default="default")
    run_parser.add_argument("--stages", help="Comma-separated stage IDs to run in the given order.")
    _add_json_argument(run_parser)

    plan_parser = subparsers.add_parser("plan", help="Run only the planner stage.")
    plan_parser.add_argument("prompt")
    plan_parser.add_argument("--workflow", default="default")
    plan_parser.add_argument("--stages", help="Comma-separated stage IDs to run in the given order.")
    _add_json_argument(plan_parser)

    implement_parser = subparsers.add_parser("implement", help="Run enabled post-planner stages using the plan file.")
    implement_parser.add_argument("--workflow", default="default")
    implement_parser.add_argument("--stages", help="Comma-separated stage IDs to run in the given order.")
    _add_json_argument(implement_parser)

    tui_parser = subparsers.add_parser("tui", help="Open an interactive stage selector.")
    tui_parser.add_argument("prompt", nargs="?")
    tui_parser.add_argument("--workflow", default="default")

    check_parser = subparsers.add_parser("check", help="Validate the loaded configuration.")
    _add_json_argument(check_parser)

    doctor_parser = subparsers.add_parser(
        "doctor", help="Diagnose provider CLIs for model-discovery drift and optionally repair it."
    )
    doctor_parser.add_argument(
        "--repair",
        action="store_true",
        help="Use a healthy provider to recover drifting providers' model lists into the cache.",
    )

    config_parser = subparsers.add_parser("config", help="Inspect configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("show", help="Print the loaded configuration.")

    tools_parser = subparsers.add_parser("tools", help="Inspect configured tools.")
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command")
    tools_subparsers.add_parser("list", help="List configured tools.")
    test_parser = tools_subparsers.add_parser("test", help="Run a configured tool with a test prompt.")
    test_parser.add_argument("name")
    _add_json_argument(test_parser)

    runs_parser = subparsers.add_parser("runs", help="Inspect previous run artifacts.")
    runs_subparsers = runs_parser.add_subparsers(dest="runs_command")
    runs_subparsers.add_parser("list", help="List previous runs.")
    show_parser = runs_subparsers.add_parser("show", help="Show details for one run.")
    show_parser.add_argument("id")

    return parser


def _add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit stable machine-readable JSON.",
    )


def _run_doctor(*, repair_enabled: bool) -> int:
    cache = ModelCache.load()
    health = diagnose(cache=cache)

    reports: list[RepairResult] | None = None
    if repair_enabled:
        if any(h.drifting for h in health):
            print("Repairing via agent backends (trying providers in order; press Ctrl-C to cancel)…\n")
        reports = repair(health, cache=cache)
        if reports:
            # Re-diagnose so the printed table reflects freshly cached lists.
            health = diagnose(cache=cache)

    _print_doctor(health, reports)
    # Fail only when a provider is installed but has no models at all — the
    # actionable "broken and not recovered" state.
    broken = [h for h in health if h.cli_present and not h.models]
    return 1 if broken else 0


def _print_doctor(health: list[ProviderHealth], reports: list[RepairResult] | None) -> None:
    print(f"{'provider':<10} {'cli':<8} {'live':<7} models")
    for h in health:
        cli = "present" if h.cli_present else "missing"
        if h.source == "static":
            live = "static"
        elif h.healthy:
            live = "ok"
        elif h.cli_present:
            live = "drift"
        else:
            live = "-"
        preview = ", ".join(h.models[:4]) + (" …" if len(h.models) > 4 else "") if h.models else "(none)"
        print(f"{h.provider:<10} {cli:<8} {live:<7} {preview}")
        if not h.healthy and h.source != "static" and h.detail:
            print(f"{'':<10} └─ {h.detail}")

    if reports is None:
        drifting = [h.provider for h in health if h.drifting]
        if drifting:
            print()
            print(f"Run 'cli-router doctor --repair' to recover: {', '.join(drifting)}")
        return

    print()
    if not reports:
        print("doctor: no drifting providers to repair.")
        return
    for report in reports:
        status = "fixed" if report.ok else "failed"
        print(f"doctor {status}: {report.provider} — {report.detail}")


def _parse_stage_names(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [stage_name.strip() for stage_name in value.split(",") if stage_name.strip()]


def _print_summary(summary: WorkflowSummary) -> int:
    print(f"run_dir: {summary.run_dir}")
    print(f"plan_path: {summary.plan_path}")
    for stage in summary.stages:
        print(f"{stage.stage_id} ({stage.tool}): exit {stage.result.returncode}")
        preview = condense_extracted(stage.extracted)
        if preview:
            for line in preview.splitlines():
                print(f"  {line}")
    if summary.error:
        print(f"error: {summary.error}", file=sys.stderr)
    print(f"exit_code: {summary.exit_code}")
    return summary.exit_code


def _emit_workflow_summary(
    config: RouterConfig,
    command: str,
    workflow: str,
    summary: WorkflowSummary,
    as_json: bool,
) -> int:
    if as_json:
        print_json_receipt(workflow_receipt(config, command, workflow, summary))
        return summary.exit_code
    return _print_summary(summary)


def _command_name(args: argparse.Namespace) -> str:
    if args.command == "tools" and getattr(args, "tools_command", None):
        return f"tools {args.tools_command}"
    return args.command or "tui"


def _error_config_path(explicit_path: str | None) -> Path | None:
    if explicit_path:
        return Path(explicit_path)
    return next((path for path in config_candidates() if path.exists()), None)


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
