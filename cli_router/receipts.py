"""Stable machine-readable CLI receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import __version__
from .config import RouterConfig, config_checksum, config_source_identity, source_checksum
from .tools import ToolTestSummary
from .workflows import StageSummary, WorkflowSummary

SCHEMA_VERSION = 1


def print_json_receipt(receipt: dict[str, Any]) -> None:
    print(json.dumps(receipt, sort_keys=True))


def version_receipt() -> dict[str, Any]:
    return _base_receipt("version")


def check_receipt(config: RouterConfig) -> dict[str, Any]:
    return _base_receipt("check", config=config)


def workflow_receipt(
    config: RouterConfig,
    command: str,
    workflow: str,
    summary: WorkflowSummary,
) -> dict[str, Any]:
    stages = [_workflow_stage_receipt(config, summary.run_dir, stage) for stage in summary.stages]
    fallback_reason = next((stage.fallback_reason for stage in summary.stages if stage.fallback_reason), None)
    artifacts = {"run_manifest": str((summary.run_dir / "run.yaml").resolve())}
    if summary.plan_path.exists():
        artifacts["plan"] = str(summary.plan_path.resolve())
    receipt = _base_receipt(
        command,
        config=config,
        workflow=workflow,
        run_id=summary.run_dir.name,
        run_dir=str(summary.run_dir.resolve()),
        exit_code=summary.exit_code,
        duration_seconds=summary.duration_seconds,
        fallback_used=fallback_reason is not None,
        fallback_reason=fallback_reason,
        artifacts=artifacts,
        stages=stages,
        error=summary.error,
    )
    return receipt


def tool_test_receipt(
    config: RouterConfig,
    name: str,
    summary: ToolTestSummary,
) -> dict[str, Any]:
    tool = config.tools[name]
    run_dir = summary.run_dir.resolve()
    stage = {
        "stage_id": name,
        "tool": name,
        "provider": tool.get("provider"),
        "model": tool.get("model"),
        "attempt": 1,
        "exit_code": summary.result.returncode,
        "failure_kind": summary.failure_kind,
        "fallback_used": False,
        "fallback_reason": None,
        "duration_seconds": summary.result.duration_seconds,
        "artifacts": {
            "stdout": str(run_dir / f"{name}.stdout"),
            "stderr": str(run_dir / f"{name}.stderr"),
        },
    }
    return _base_receipt(
        "tools test",
        config=config,
        run_id=summary.run_dir.name,
        run_dir=str(run_dir),
        exit_code=summary.result.returncode,
        duration_seconds=summary.result.duration_seconds,
        artifacts={"run_manifest": str(run_dir / "run.yaml")},
        stages=[stage],
    )


def error_receipt(command: str, error: str, config_path: str | Path | None = None) -> dict[str, Any]:
    source: str | None = None
    checksum: str | None = None
    if config_path is not None:
        path = Path(config_path).resolve()
        source = str(path)
        checksum = source_checksum(path)
    return _base_receipt(
        command,
        config_identity={"source": source, "checksum": checksum},
        exit_code=2,
        error=error,
    )


def _base_receipt(
    command: str,
    *,
    config: RouterConfig | None = None,
    config_identity: dict[str, str | None] | None = None,
    workflow: str | None = None,
    run_id: str | None = None,
    run_dir: str | None = None,
    exit_code: int = 0,
    duration_seconds: float = 0.0,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
    artifacts: dict[str, str] | None = None,
    stages: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    if config is not None:
        config_identity = {
            "source": config_source_identity(config),
            "checksum": config_checksum(config),
        }
    if config_identity is None:
        config_identity = {"source": None, "checksum": None}
    return {
        "schema_version": SCHEMA_VERSION,
        "cli_router_version": __version__,
        "command": command,
        "config": config_identity,
        "workflow": workflow,
        "run_id": run_id,
        "run_dir": run_dir,
        "overall_outcome": "success" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "duration_seconds": duration_seconds,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "artifacts": artifacts or {},
        "stages": stages or [],
        "error": error,
    }


def _workflow_stage_receipt(
    config: RouterConfig,
    run_dir: Path,
    stage: StageSummary,
) -> dict[str, Any]:
    tool = config.tools[stage.tool]
    prefix = stage.stage_id if stage.attempt == 1 else f"{stage.stage_id}.{stage.tool}"
    artifact_root = run_dir.resolve()
    artifacts = {
        "stdout": str(artifact_root / f"{prefix}.stdout"),
        "stderr": str(artifact_root / f"{prefix}.stderr"),
    }
    if stage.extracted is not None:
        artifacts["extracted"] = str(artifact_root / f"{prefix}.extracted.md")
    receipt = {
        "stage_id": stage.stage_id,
        "tool": stage.tool,
        "provider": tool.get("provider"),
        "model": tool.get("model"),
        "attempt": stage.attempt,
        "exit_code": stage.result.returncode,
        "failure_kind": stage.failure_kind,
        "fallback_used": stage.fallback_attempt is not None,
        "fallback_reason": stage.fallback_reason,
        "duration_seconds": stage.duration_seconds,
        "artifacts": artifacts,
    }
    if stage.fallback_attempt is not None:
        receipt.update(
            {
                "primary_tool": stage.primary_tool,
                "primary_failure_kind": stage.primary_failure_kind,
                "fallback_tool": stage.tool,
                "fallback_attempt": stage.fallback_attempt,
            }
        )
    return receipt
