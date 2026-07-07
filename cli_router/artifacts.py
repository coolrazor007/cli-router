"""Run artifact persistence."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .runner import ToolRunResult


def create_run_dir(run_root: str | Path) -> Path:
    root = Path(run_root)
    root.mkdir(parents=True, exist_ok=True)

    base = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    candidate = root / base
    counter = 1
    while candidate.exists():
        candidate = root / f"{base}-{counter}"
        counter += 1
    candidate.mkdir()
    return candidate


def write_stage_artifacts(run_dir: Path, stage_id: str, result: ToolRunResult, extracted: str | None = None) -> None:
    (run_dir / f"{stage_id}.stdout").write_text(result.stdout, encoding="utf-8")
    (run_dir / f"{stage_id}.stderr").write_text(result.stderr, encoding="utf-8")
    if extracted is not None:
        (run_dir / f"{stage_id}.extracted.md").write_text(extracted, encoding="utf-8")


def write_run_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    serializable = _serialize(manifest)
    (run_dir / "run.yaml").write_text(yaml.safe_dump(serializable, sort_keys=False), encoding="utf-8")


def _serialize(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return _serialize(asdict(value))
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value
