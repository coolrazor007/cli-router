"""Run history inspection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import RouterConfig


@dataclass(frozen=True)
class RunInfo:
    id: str
    path: Path
    workflow: str
    exit_code: int | None
    user_prompt: str
    stages: list[dict[str, Any]]
    error: str | None = None


@dataclass(frozen=True)
class RunDetail:
    id: str
    path: Path
    manifest: dict[str, Any]
    artifacts: list[str]


def list_runs(config: RouterConfig) -> list[RunInfo]:
    root = _run_root(config)
    if not root.exists():
        return []

    runs: list[RunInfo] = []
    for path in sorted((entry for entry in root.iterdir() if entry.is_dir()), key=lambda entry: entry.name, reverse=True):
        manifest, error = _read_manifest(path)
        if error:
            runs.append(
                RunInfo(
                    id=path.name,
                    path=path,
                    workflow="unknown",
                    exit_code=None,
                    user_prompt="",
                    stages=[],
                    error=error,
                )
            )
            continue

        runs.append(
            RunInfo(
                id=path.name,
                path=path,
                workflow=str(manifest.get("workflow") or manifest.get("command") or "unknown"),
                exit_code=_exit_code(manifest.get("exit_code")),
                user_prompt=str(manifest.get("user_prompt") or manifest.get("prompt") or ""),
                stages=_stages(manifest.get("stages")),
            )
        )
    return runs


def show_run(config: RouterConfig, run_id: str) -> RunDetail:
    path = _resolve_run_path(_run_root(config), run_id)
    manifest, error = _read_manifest(path)
    if error:
        raise KeyError(f"Invalid run manifest: {path.name}")
    artifacts = sorted(entry.name for entry in path.iterdir() if entry.is_file())
    return RunDetail(id=path.name, path=path, manifest=manifest, artifacts=artifacts)


def _run_root(config: RouterConfig) -> Path:
    return Path(config.defaults.get("run_dir", ".cli-router/runs"))


def _resolve_run_path(root: Path, run_id: str) -> Path:
    exact = root / run_id
    if exact.is_dir():
        return exact
    if not root.exists():
        raise KeyError(f"Unknown run: {run_id}")

    matches = sorted(entry for entry in root.iterdir() if entry.is_dir() and entry.name.startswith(run_id))
    if not matches:
        raise KeyError(f"Unknown run: {run_id}")
    if len(matches) > 1:
        ids = ", ".join(entry.name for entry in matches)
        raise KeyError(f"Ambiguous run: {run_id} matches {ids}")
    return matches[0]


def _read_manifest(path: Path) -> tuple[dict[str, Any], str | None]:
    manifest_path = path / "run.yaml"
    if not manifest_path.exists():
        return {}, "missing_manifest"
    try:
        loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}, "invalid_manifest"
    if not isinstance(loaded, dict):
        return {}, "invalid_manifest"
    return loaded, None


def _exit_code(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _stages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [stage for stage in value if isinstance(stage, dict)]
