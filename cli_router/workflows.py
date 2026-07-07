"""Workflow execution for planner and coder stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import create_run_dir, write_run_manifest, write_stage_artifacts
from .config import RouterConfig
from .extractors import ExtractionError, extract_output
from .failures import classify_failure, stage_failure_message
from .runner import ToolRunResult, run_tool


@dataclass(frozen=True)
class StageSummary:
    stage_id: str
    tool: str
    result: ToolRunResult
    extracted: str | None = None
    failure_kind: str | None = None


@dataclass
class WorkflowSummary:
    run_dir: Path
    plan_path: Path
    exit_code: int = 0
    stages: list[StageSummary] = field(default_factory=list)
    error: str | None = None


def plan_workflow(config: RouterConfig, user_prompt: str, workflow_name: str = "default") -> WorkflowSummary:
    workflow = _workflow(config, workflow_name)
    run_dir = create_run_dir(_default(config, "run_dir", ".cli-router/runs"))
    plan_path = Path(_default(config, "plan_file", "PLAN.md"))
    summary = WorkflowSummary(run_dir=run_dir, plan_path=plan_path)
    planner_stage = _stage(workflow, "planner", index=0)
    _run_stage(config, summary, planner_stage, user_prompt, write_plan=True)
    _finalize(summary, user_prompt, workflow_name)
    return summary


def implement_workflow(config: RouterConfig, workflow_name: str = "default") -> WorkflowSummary:
    workflow = _workflow(config, workflow_name)
    run_dir = create_run_dir(_default(config, "run_dir", ".cli-router/runs"))
    plan_path = Path(_default(config, "plan_file", "PLAN.md"))
    summary = WorkflowSummary(run_dir=run_dir, plan_path=plan_path)

    if not plan_path.exists():
        summary.exit_code = 2
        summary.error = f"Plan file does not exist: {plan_path}"
        _finalize(summary, "", workflow_name)
        return summary

    coder_stage = _stage(workflow, "coder", index=1)
    _run_stage(config, summary, coder_stage, "", write_plan=False)
    _finalize(summary, "", workflow_name)
    return summary


def run_workflow(config: RouterConfig, user_prompt: str, workflow_name: str = "default") -> WorkflowSummary:
    workflow = _workflow(config, workflow_name)
    run_dir = create_run_dir(_default(config, "run_dir", ".cli-router/runs"))
    plan_path = Path(_default(config, "plan_file", "PLAN.md"))
    summary = WorkflowSummary(run_dir=run_dir, plan_path=plan_path)
    stop_on_failure = bool(_default(config, "stop_on_failure", True))

    planner_stage = _stage(workflow, "planner", index=0)
    _run_stage(config, summary, planner_stage, user_prompt, write_plan=True)
    if summary.exit_code and stop_on_failure:
        _finalize(summary, user_prompt, workflow_name)
        return summary

    coder_stage = _stage(workflow, "coder", index=1)
    _run_stage(config, summary, coder_stage, user_prompt, write_plan=False)
    _finalize(summary, user_prompt, workflow_name)
    return summary


def _run_stage(
    config: RouterConfig,
    summary: WorkflowSummary,
    stage: dict[str, Any],
    user_prompt: str,
    *,
    write_plan: bool,
) -> None:
    stage_id = str(stage["id"])
    rendered_input = _render_template(
        str(stage.get("input_template", "{user_prompt}")),
        user_prompt=user_prompt,
        plan_path=str(summary.plan_path),
    )
    tool_names = [str(stage["tool"]), *[str(tool_name) for tool_name in stage.get("fallback_tools", [])]]

    for attempt_index, tool_name in enumerate(tool_names):
        tool = config.tools[tool_name]
        result = run_tool(
            tool,
            {
                "prompt": rendered_input,
                "user_prompt": user_prompt,
                "plan_path": str(summary.plan_path),
            },
        )

        extracted: str | None = None
        failure_kind = classify_failure(result)
        if result.returncode == 0:
            try:
                extracted = extract_output(result.stdout, tool.get("output"))
                summary.exit_code = 0
                summary.error = None
            except ExtractionError as exc:
                summary.exit_code = 3
                summary.error = str(exc)
                failure_kind = "extraction_failed"
        else:
            summary.exit_code = result.returncode
            summary.error = stage_failure_message(stage_id, result)

        artifact_prefix = stage_id if attempt_index == 0 else f"{stage_id}.{tool_name}"
        write_stage_artifacts(summary.run_dir, artifact_prefix, result, extracted)
        summary.stages.append(StageSummary(stage_id, tool_name, result, extracted, failure_kind))

        if summary.exit_code == 0:
            if write_plan and extracted is not None:
                output_file = Path(stage.get("output_file") or summary.plan_path)
                summary.plan_path = output_file
                output_file.write_text(extracted, encoding="utf-8")
            return


def _workflow(config: RouterConfig, name: str) -> dict[str, Any]:
    try:
        return config.workflows[name]
    except KeyError as exc:
        raise KeyError(f"Unknown workflow: {name}") from exc


def _stage(workflow: dict[str, Any], stage_id: str, *, index: int) -> dict[str, Any]:
    stages = workflow.get("stages", [])
    for stage in stages:
        if stage.get("id") == stage_id:
            return stage
    try:
        return stages[index]
    except IndexError as exc:
        raise KeyError(f"Workflow is missing {stage_id} stage") from exc


def _default(config: RouterConfig, key: str, fallback: Any) -> Any:
    return config.defaults.get(key, fallback)


def _render_template(template: str, **variables: str) -> str:
    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def _finalize(summary: WorkflowSummary, user_prompt: str, workflow_name: str) -> None:
    write_run_manifest(
        summary.run_dir,
        {
            "workflow": workflow_name,
            "user_prompt": user_prompt,
            "plan_path": summary.plan_path,
            "exit_code": summary.exit_code,
            "error": summary.error,
            "stages": summary.stages,
        },
    )
