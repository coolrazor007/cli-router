"""Output extraction helpers for external CLI results."""

from __future__ import annotations

import json
from typing import Any


class ExtractionError(RuntimeError):
    """Raised when tool output cannot be extracted as configured."""


def extract_output(stdout: str, output_config: dict[str, Any] | None = None) -> str:
    config = output_config or {}
    output_format = config.get("format", "text")

    if output_format == "text":
        return stdout
    if output_format == "json":
        return _extract_json(stdout, config.get("extract"))

    raise ExtractionError(f"Unsupported output format: {output_format}")


def _extract_json(stdout: str, path: str | None) -> str:
    try:
        value: Any = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Invalid JSON output: {exc}") from exc

    if path:
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ExtractionError(f"Missing JSON extraction path: {path}")
            value = value[part]

    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, sort_keys=True)
