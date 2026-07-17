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

AUTH_REQUIRED_PATTERNS = (
    "not logged in",
    "please run /login",
    "authentication required",
    "login required",
    "not authenticated",
    "unauthorized",
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
    if any(pattern in combined for pattern in AUTH_REQUIRED_PATTERNS):
        return "auth_required"
    if result.returncode == 127:
        return "command_not_found"
    return "command_failed"


def stage_failure_message(
    stage_id: str, result: ToolRunResult, failure_kind: str | None = None
) -> str:
    if failure_kind is None:
        failure_kind = classify_failure(result)
    if failure_kind == "usage_limit":
        return f"Stage {stage_id!r} failed because the provider reported a usage limit"
    if failure_kind == "command_not_found":
        return f"Stage {stage_id!r} failed because the command was not found"
    if failure_kind == "timeout":
        return f"Stage {stage_id!r} failed because the command timed out"
    if failure_kind == "unsupported_model":
        return f"Stage {stage_id!r} failed because the configured model is not supported by the provider"
    if failure_kind == "auth_required":
        return _with_provider_message(
            f"Stage {stage_id!r} failed because provider authentication is required",
            result,
        )
    return _with_provider_message(f"Stage {stage_id!r} failed with exit code {result.returncode}", result)


def _with_provider_message(message: str, result: ToolRunResult) -> str:
    provider_message = _provider_message(result)
    if not provider_message:
        return message
    return f"{message}: {provider_message}"


def _provider_message(result: ToolRunResult) -> str | None:
    for stream in (result.stderr, result.stdout):
        for line in stream.splitlines():
            cleaned = line.strip()
            if cleaned:
                return cleaned
    return None
