"""Failure helpers."""

from __future__ import annotations

from .runner import ToolRunResult


USAGE_LIMIT_PATTERNS = (
    "usage limit",
    "session limit",
    "hit your limit",
    "hit your session limit",
    "rate limit",
    "quota exceeded",
    "credit balance is too low",
    "too many requests",
    "429",
)

UNSUPPORTED_MODEL_PATTERNS = (
    "model is not supported",
    "model not supported",
    "unsupported model",
)


def classify_failure(result: ToolRunResult) -> str | None:
    if result.returncode == 0:
        return None
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == 124 or "timed out" in combined:
        return "timeout"
    if any(pattern in combined for pattern in USAGE_LIMIT_PATTERNS):
        return "usage_limit"
    if any(pattern in combined for pattern in UNSUPPORTED_MODEL_PATTERNS):
        return "unsupported_model"
    if result.returncode == 127:
        return "command_not_found"
    return "command_failed"


def stage_failure_message(stage_id: str, result: ToolRunResult) -> str:
    failure_kind = classify_failure(result)
    if failure_kind == "usage_limit":
        return f"Stage {stage_id!r} failed because the provider reported a usage limit"
    if failure_kind == "command_not_found":
        return f"Stage {stage_id!r} failed because the command was not found"
    if failure_kind == "timeout":
        return f"Stage {stage_id!r} failed because the command timed out"
    if failure_kind == "unsupported_model":
        return f"Stage {stage_id!r} failed because the configured model is not supported by the provider"
    return f"Stage {stage_id!r} failed with exit code {result.returncode}"
