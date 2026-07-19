"""Workflow execution for configured stages."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Sequence

from .artifacts import create_run_dir, write_run_manifest, write_stage_artifacts
from .config import RouterConfig
from .extractors import ExtractionError, extract_output
from .failures import FALLBACK_SAFE_FAILURE_KINDS, classify_failure, stage_failure_message
from .logs import append_run_metrics, configure_logging, key_values
from .runner import ToolRunResult, render_placeholders, run_tool, stream_tool


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class StageSummary:
    stage_id: str
    tool: str
    result: ToolRunResult
    extracted: str | None = None
    failure_kind: str | None = None
    attempt: int = 1
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    primary_tool: str | None = None
    primary_failure_kind: str | None = None
    trigger_tool: str | None = None
    trigger_failure_kind: str | None = None
    fallback_reason: str | None = None
    fallback_attempt: int | None = None


@dataclass
class WorkflowSummary:
    run_dir: Path
    plan_path: Path
    exit_code: int = 0
    stages: list[StageSummary] = field(default_factory=list)
    error: str | None = None
    started_at: str = field(default_factory=_now_iso)
    finished_at: str = ""
    duration_seconds: float = 0.0


class WorkflowObserver(Protocol):
    def stage_started(self, stage_id: str, tool: str, attempt: int) -> None: ...

    def stage_output(self, stage_id: str, tool: str, line: str) -> None: ...

    def stage_error(self, stage_id: str, tool: str, line: str) -> None: ...

    def stage_finished(self, stage: StageSummary) -> None: ...


def plan_workflow(
    config: RouterConfig,
    user_prompt: str,
    workflow_name: str = "default",
    stage_names: Sequence[str] | None = None,
    observer: WorkflowObserver | None = None,
) -> WorkflowSummary:
    logger = configure_logging(config)
    workflow = _workflow(config, workflow_name)
    run_dir = create_run_dir(_default(config, "run_dir", ".cli-router/runs"))
    plan_path = Path(_default(config, "plan_file", "PLAN.md"))
    summary = WorkflowSummary(run_dir=run_dir, plan_path=plan_path)
    _log_workflow_started(logger, summary, user_prompt, workflow_name, config)

    stages = _named_stages(workflow, stage_names) if stage_names else [_stage(workflow, "planner", index=0)]
    _run_stages(config, summary, stages, user_prompt, observer=observer)
    _finalize(config, summary, user_prompt, workflow_name, logger)
    return summary


def implement_workflow(
    config: RouterConfig,
    workflow_name: str = "default",
    stage_names: Sequence[str] | None = None,
    observer: WorkflowObserver | None = None,
) -> WorkflowSummary:
    logger = configure_logging(config)
    workflow = _workflow(config, workflow_name)
    run_dir = create_run_dir(_default(config, "run_dir", ".cli-router/runs"))
    plan_path = Path(_default(config, "plan_file", "PLAN.md"))
    summary = WorkflowSummary(run_dir=run_dir, plan_path=plan_path)
    _log_workflow_started(logger, summary, "", workflow_name, config)

    if not plan_path.exists():
        summary.exit_code = 2
        summary.error = f"Plan file does not exist: {plan_path}"
        _finalize(config, summary, "", workflow_name, logger)
        return summary

    stages = _named_stages(workflow, stage_names) if stage_names else _implement_stages(workflow)
    if not stages:
        summary.exit_code = 2
        summary.error = "Workflow has no enabled post-planner stages"
        _finalize(config, summary, "", workflow_name, logger)
        return summary
    _run_stages(config, summary, stages, "", observer=observer)
    _finalize(config, summary, "", workflow_name, logger)
    return summary


def run_workflow(
    config: RouterConfig,
    user_prompt: str,
    workflow_name: str = "default",
    stage_names: Sequence[str] | None = None,
    observer: WorkflowObserver | None = None,
) -> WorkflowSummary:
    logger = configure_logging(config)
    workflow = _workflow(config, workflow_name)
    run_dir = create_run_dir(_default(config, "run_dir", ".cli-router/runs"))
    plan_path = Path(_default(config, "plan_file", "PLAN.md"))
    summary = WorkflowSummary(run_dir=run_dir, plan_path=plan_path)
    _log_workflow_started(logger, summary, user_prompt, workflow_name, config)
    stages = _named_stages(workflow, stage_names) if stage_names else _enabled_stages(workflow)
    if not stages:
        summary.exit_code = 2
        summary.error = "Workflow has no enabled stages"
        _finalize(config, summary, user_prompt, workflow_name, logger)
        return summary
    _run_stages(
        config,
        summary,
        stages,
        user_prompt,
        stop_on_failure=bool(_default(config, "stop_on_failure", True)),
        observer=observer,
    )
    _finalize(config, summary, user_prompt, workflow_name, logger)
    return summary


def _run_stages(
    config: RouterConfig,
    summary: WorkflowSummary,
    stages: Sequence[dict[str, Any]],
    user_prompt: str,
    *,
    stop_on_failure: bool = True,
    observer: WorkflowObserver | None = None,
) -> None:
    logger = logging.getLogger("cli_router")
    first_failure: tuple[int, str | None] | None = None
    for stage in stages:
        _run_stage(config, summary, stage, user_prompt, observer=observer, logger=logger)
        if summary.exit_code:
            if first_failure is None:
                first_failure = (summary.exit_code, summary.error)
            if stop_on_failure:
                return
    if first_failure is not None:
        summary.exit_code, summary.error = first_failure


def _run_stage(
    config: RouterConfig,
    summary: WorkflowSummary,
    stage: dict[str, Any],
    user_prompt: str,
    *,
    observer: WorkflowObserver | None = None,
    logger: logging.Logger,
) -> None:
    stage_id = str(stage["id"])
    previous_output, all_stage_outputs = _accumulated_outputs(summary)
    rendered_input = _render_template(
        str(stage.get("input_template", "{user_prompt}")),
        user_prompt=user_prompt,
        plan_path=str(summary.plan_path),
        previous_output=previous_output,
        all_stage_outputs=all_stage_outputs,
    )
    primary_tool = str(stage["tool"])
    fallback_policies = [_fallback_policy(value) for value in stage.get("fallback_tools", [])]
    max_fallback_attempts = int(stage.get("max_fallback_attempts", len(fallback_policies)))
    candidates = [(primary_tool, None), *fallback_policies]
    fallback_attempts_started = 0
    subprocess_attempt = 0
    current_failure_kind: str | None = None
    current_failure_tool: str | None = None
    primary_failure_kind: str | None = None

    for tool_name, allowed_failure_kinds in candidates:
        is_fallback = allowed_failure_kinds is not None
        trigger_tool: str | None = None
        trigger_failure_kind: str | None = None
        if is_fallback:
            assert allowed_failure_kinds is not None
            if current_failure_kind not in FALLBACK_SAFE_FAILURE_KINDS:
                return
            if current_failure_kind not in allowed_failure_kinds:
                continue
            if fallback_attempts_started >= max_fallback_attempts:
                return
            fallback_attempts_started += 1
            trigger_tool = current_failure_tool
            trigger_failure_kind = current_failure_kind

        subprocess_attempt += 1
        attempt = subprocess_attempt
        started_at = _now_iso()
        started = time.monotonic()
        tool = config.tools[tool_name]
        logger.info(
            "stage_started %s",
            key_values(
                run_id=summary.run_dir.name,
                stage=stage_id,
                tool=tool_name,
                attempt=attempt,
                fallback=is_fallback,
            ),
        )
        variables = {
            "prompt": rendered_input,
            "user_prompt": user_prompt,
            "plan_path": str(summary.plan_path),
            "previous_output": previous_output,
            "all_stage_outputs": all_stage_outputs,
            "target_root": str(Path.cwd().resolve()),
        }
        if observer is not None:
            observer.stage_started(stage_id, tool_name, attempt)

            def on_stdout_line(line: str, current_observer: WorkflowObserver = observer) -> None:
                current_observer.stage_output(stage_id, tool_name, line)

            def on_stderr_line(line: str, current_observer: WorkflowObserver = observer) -> None:
                current_observer.stage_error(stage_id, tool_name, line)

            result = stream_tool(
                tool,
                variables,
                on_stdout_line=on_stdout_line,
                on_stderr_line=on_stderr_line,
            )
        else:
            result = run_tool(tool, variables)

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
            summary.error = stage_failure_message(stage_id, result, failure_kind)

        artifact_prefix = stage_id if not is_fallback else f"{stage_id}.{tool_name}"
        write_stage_artifacts(summary.run_dir, artifact_prefix, result, extracted)
        finished_at = _now_iso()
        duration_seconds = _elapsed(started)
        stage_summary = StageSummary(
            stage_id=stage_id,
            tool=tool_name,
            result=result,
            extracted=extracted,
            failure_kind=failure_kind,
            attempt=attempt,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            primary_tool=primary_tool if is_fallback else None,
            primary_failure_kind=primary_failure_kind if is_fallback else None,
            trigger_tool=trigger_tool,
            trigger_failure_kind=trigger_failure_kind,
            fallback_reason="allowed_failure_kind" if is_fallback else None,
            fallback_attempt=fallback_attempts_started if is_fallback else None,
        )
        summary.stages.append(stage_summary)
        logger.info(
            "stage_finished %s",
            key_values(
                run_id=summary.run_dir.name,
                stage=stage_id,
                tool=tool_name,
                attempt=attempt,
                exit_code=result.returncode,
                failure_kind=failure_kind or "none",
                duration_seconds=duration_seconds,
                subprocess_seconds=result.duration_seconds,
                stdout_bytes=_byte_count(result.stdout),
                stderr_bytes=_byte_count(result.stderr),
            ),
        )
        if observer is not None:
            observer.stage_finished(stage_summary)

        if summary.exit_code == 0:
            if extracted is not None and stage.get("output_file"):
                output_file = Path(str(stage["output_file"]))
                output_file.write_text(extracted, encoding="utf-8")
                if _stage_updates_plan(stage, output_file, summary.plan_path):
                    summary.plan_path = output_file
            return
        current_failure_kind = failure_kind
        current_failure_tool = tool_name
        if not is_fallback:
            primary_failure_kind = failure_kind


def _fallback_policy(value: Any) -> tuple[str, frozenset[str]]:
    if isinstance(value, str):
        return value, FALLBACK_SAFE_FAILURE_KINDS
    return str(value["tool"]), frozenset(str(item) for item in value["on"])


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


def _workflow_stages(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return list(workflow.get("stages", []))


def _enabled_stages(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [stage for stage in _workflow_stages(workflow) if stage.get("enabled", True) is not False]


def _implement_stages(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    stages = _workflow_stages(workflow)
    planner_index = next((index for index, stage in enumerate(stages) if stage.get("id") == "planner"), 0)
    return [stage for index, stage in enumerate(stages) if index > planner_index and stage.get("enabled", True) is not False]


def _named_stages(workflow: dict[str, Any], stage_names: Sequence[str]) -> list[dict[str, Any]]:
    stages_by_id = {str(stage["id"]): stage for stage in _workflow_stages(workflow)}
    selected: list[dict[str, Any]] = []
    for stage_name in stage_names:
        try:
            selected.append(stages_by_id[stage_name])
        except KeyError as exc:
            raise KeyError(f"Unknown stage: {stage_name}") from exc
    return selected


def _stage_updates_plan(stage: dict[str, Any], output_file: Path, current_plan_path: Path) -> bool:
    if "updates_plan" in stage:
        return bool(stage["updates_plan"])
    return stage.get("id") == "planner" or output_file == current_plan_path


def _default(config: RouterConfig, key: str, fallback: Any) -> Any:
    return config.defaults.get(key, fallback)


def _render_template(template: str, **variables: str) -> str:
    return render_placeholders(template, variables)


def _accumulated_outputs(summary: WorkflowSummary) -> tuple[str, str]:
    """Collect prior stages' extracted outputs for downstream template variables.

    ``previous_output`` is the extracted final answer of the most recent
    successfully-extracted stage; ``all_stage_outputs`` is every such output so
    far, each labeled with its stage id. Attempts that produced no extracted
    text (failures, fallbacks) are skipped, so a stage only forwards the answer
    a downstream stage can actually use. Both are empty strings for the first
    stage, which has no predecessor.
    """
    completed = [stage for stage in summary.stages if stage.extracted]
    if not completed:
        return "", ""
    previous_output = completed[-1].extracted or ""
    all_stage_outputs = "\n\n".join(
        f"## {stage.stage_id}\n{stage.extracted}" for stage in completed
    )
    return previous_output, all_stage_outputs


def _finalize(
    config: RouterConfig,
    summary: WorkflowSummary,
    user_prompt: str,
    workflow_name: str,
    logger: logging.Logger,
) -> None:
    summary.finished_at = _now_iso()
    summary.duration_seconds = _duration_between(summary.started_at, summary.finished_at)
    metrics = _metrics(summary, workflow_name)
    write_run_manifest(
        summary.run_dir,
        {
            "workflow": workflow_name,
            "user_prompt": user_prompt,
            "plan_path": summary.plan_path,
            "exit_code": summary.exit_code,
            "error": summary.error,
            "started_at": summary.started_at,
            "finished_at": summary.finished_at,
            "duration_seconds": summary.duration_seconds,
            "metrics": metrics,
            "stages": [_stage_manifest_entry(stage) for stage in summary.stages],
        },
    )
    append_run_metrics(config, metrics)
    logger.info(
        "workflow_finished %s",
        key_values(
            run_id=summary.run_dir.name,
            workflow=workflow_name,
            exit_code=summary.exit_code,
            duration_seconds=summary.duration_seconds,
            stage_count=len(summary.stages),
            retry_count=metrics["retry_count"],
        ),
    )


def _log_workflow_started(
    logger: logging.Logger,
    summary: WorkflowSummary,
    user_prompt: str,
    workflow_name: str,
    config: RouterConfig,
) -> None:
    logger.info(
        "workflow_started %s",
        key_values(
            run_id=summary.run_dir.name,
            workflow=workflow_name,
            run_dir=summary.run_dir,
            plan_path=summary.plan_path,
            config_source=config.source or "built-in",
            prompt=_truncate(user_prompt.replace("\n", " "), 200),
        ),
    )


def _metrics(summary: WorkflowSummary, workflow_name: str) -> dict[str, Any]:
    stages = [_stage_metrics(stage) for stage in summary.stages]
    return {
        "run_id": summary.run_dir.name,
        "workflow": workflow_name,
        "started_at": summary.started_at,
        "finished_at": summary.finished_at,
        "total_duration_seconds": summary.duration_seconds,
        "exit_code": summary.exit_code,
        "stage_count": len(summary.stages),
        "retry_count": sum(1 for stage in summary.stages if stage.attempt > 1),
        "stages": stages,
    }


def _stage_manifest_entry(stage: StageSummary) -> dict[str, Any]:
    """Compact per-stage record for ``run.yaml``.

    A stage's full ``stdout``/``stderr`` (and extracted answer) are already
    persisted as sidecar artifact files next to the manifest, so the manifest
    stores byte counts and the artifact filenames instead of duplicating
    potentially huge streams inline. The rendered ``command`` is kept — it is
    the only record of the exact prompt sent to the tool. Artifact filenames
    mirror the prefix used by ``write_stage_artifacts`` (``<stage>`` for the
    first attempt, ``<stage>.<tool>`` for fallback attempts).
    """
    prefix = stage.stage_id if stage.attempt == 1 else f"{stage.stage_id}.{stage.tool}"
    artifacts = {"stdout": f"{prefix}.stdout", "stderr": f"{prefix}.stderr"}
    if stage.extracted is not None:
        artifacts["extracted"] = f"{prefix}.extracted.md"
    entry = {
        "stage_id": stage.stage_id,
        "tool": stage.tool,
        "attempt": stage.attempt,
        "command": stage.result.command,
        "exit_code": stage.result.returncode,
        "failure_kind": stage.failure_kind,
        "started_at": stage.started_at,
        "finished_at": stage.finished_at,
        "duration_seconds": stage.duration_seconds,
        "subprocess_seconds": stage.result.duration_seconds,
        "stdout_bytes": _byte_count(stage.result.stdout),
        "stderr_bytes": _byte_count(stage.result.stderr),
        "extracted_bytes": _byte_count(stage.extracted) if stage.extracted is not None else 0,
        "artifacts": artifacts,
    }
    if stage.fallback_attempt is not None:
        entry.update(
            {
                "primary_tool": stage.primary_tool,
                "primary_failure_kind": stage.primary_failure_kind,
                "trigger_tool": stage.trigger_tool,
                "trigger_failure_kind": stage.trigger_failure_kind,
                "fallback_tool": stage.tool,
                "fallback_reason": stage.fallback_reason,
                "fallback_attempt": stage.fallback_attempt,
            }
        )
    return entry


def _stage_metrics(stage: StageSummary) -> dict[str, Any]:
    return {
        "stage_id": stage.stage_id,
        "tool": stage.tool,
        "attempt": stage.attempt,
        "started_at": stage.started_at,
        "finished_at": stage.finished_at,
        "exit_code": stage.result.returncode,
        "failure_kind": stage.failure_kind,
        "duration_seconds": stage.duration_seconds,
        "subprocess_seconds": stage.result.duration_seconds,
        "stdout_bytes": _byte_count(stage.result.stdout),
        "stderr_bytes": _byte_count(stage.result.stderr),
    }


def _elapsed(started: float) -> float:
    return max(0.0, time.monotonic() - started)


def _duration_between(started_at: str, finished_at: str) -> float:
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return 0.0
    return max(0.0, (finished - started).total_seconds())


def _byte_count(value: str) -> int:
    return len(value.encode("utf-8"))


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
