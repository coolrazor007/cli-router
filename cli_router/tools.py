"""Tool inspection commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .artifacts import create_run_dir, write_run_manifest, write_stage_artifacts
from .config import RouterConfig
from .runner import ToolRunResult, run_tool


@dataclass(frozen=True)
class ToolTestSummary:
    run_dir: Path
    result: ToolRunResult


def list_tools(config: RouterConfig) -> list[str]:
    return sorted(config.tools)


def test_tool(config: RouterConfig, name: str, prompt: str = "CLI-Router tool test") -> ToolTestSummary:
    if name not in config.tools:
        raise KeyError(f"Unknown tool: {name}")
    run_dir = create_run_dir(config.defaults.get("run_dir", ".cli-router/runs"))
    result = run_tool(config.tools[name], {"prompt": prompt, "user_prompt": prompt, "plan_path": "PLAN.md"})
    write_stage_artifacts(run_dir, name, result)
    write_run_manifest(
        run_dir,
        {
            "command": "tools test",
            "tool": name,
            "prompt": prompt,
            "exit_code": result.returncode,
            "result": result,
        },
    )
    return ToolTestSummary(run_dir, result)
