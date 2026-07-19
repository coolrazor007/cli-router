"""Tool inspection commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .artifacts import create_run_dir, write_run_manifest, write_stage_artifacts
from .config import RouterConfig, config_identity
from .failures import classify_failure
from .runner import ToolRunResult, run_tool


@dataclass(frozen=True)
class ToolTestSummary:
    run_dir: Path
    result: ToolRunResult
    failure_kind: str | None = None


def list_tools(config: RouterConfig) -> list[str]:
    return sorted(config.tools)


def test_tool(config: RouterConfig, name: str, prompt: str = "CLI-Router tool test") -> ToolTestSummary:
    if name not in config.tools:
        raise KeyError(f"Unknown tool: {name}")
    run_dir = create_run_dir(config.defaults.get("run_dir", ".cli-router/runs"))
    result = run_tool(
        config.tools[name],
        {
            "prompt": prompt,
            "user_prompt": prompt,
            "plan_path": "PLAN.md",
            "target_root": str(Path.cwd().resolve()),
        },
    )
    failure_kind = classify_failure(result)
    write_stage_artifacts(run_dir, name, result)
    write_run_manifest(
        run_dir,
        {
            "command": "tools test",
            "run_id": run_dir.name,
            "config": config_identity(config),
            "tool": name,
            "prompt": prompt,
            "exit_code": result.returncode,
            "failure_kind": failure_kind,
            "duration_seconds": result.duration_seconds,
            "result": result,
        },
    )
    return ToolTestSummary(run_dir, result, failure_kind)
