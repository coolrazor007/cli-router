"""Provider and model helpers for TUI configuration."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from typing import Any


PROVIDERS = ("codex", "claude", "hermes", "grok")

DEFAULT_MODELS = {
    "codex": ["gpt-5.5", "gpt-5.1", "gpt-5"],
    "claude": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
    "hermes": ["hermes-auto"],
    "grok": ["grok-build"],
}

MODEL_LIST_COMMANDS = {
    "codex": (["codex", "debug", "models"],),
    "claude": (["claude", "models"], ["claude", "model", "list"]),
    "hermes": (["hermes", "models"], ["hermes", "model", "list"]),
    "grok": (["grok", "models"],),
}

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def model_options_for_provider(provider: str, runner: CommandRunner | None = None) -> list[str]:
    runner = runner or subprocess.run
    discovered = _discover_models(provider, runner)
    return discovered or list(DEFAULT_MODELS.get(provider, ["default"]))


def provider_tool_config(provider: str, model: str, effort: str = "medium") -> dict[str, Any]:
    return {
        "type": provider,
        "provider": provider,
        "model": model,
        "effort": effort,
        "command": _provider_command(provider),
        "output": {"format": "text"},
    }


def _provider_command(provider: str) -> list[str]:
    if provider == "codex":
        return ["codex", "exec", "{prompt}"]
    if provider == "claude":
        return ["claude", "-p", "{prompt}"]
    if provider == "hermes":
        return ["hermes", "run", "{prompt}"]
    if provider == "grok":
        return ["grok", "--single", "{prompt}"]
    return [provider, "{prompt}"]


def _discover_models(provider: str, runner: CommandRunner) -> list[str]:
    models: list[str] = []
    for command in MODEL_LIST_COMMANDS.get(provider, ()):
        try:
            completed = runner(
                command,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=1.5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        models.extend(_parse_model_catalog(completed.stdout))
        if models:
            return models
        models.extend(_parse_model_output(completed.stdout))
        if models:
            return models
    return []


def _parse_model_catalog(output: str) -> list[str]:
    json_start = output.find("{")
    if json_start == -1:
        return []
    try:
        catalog = json.loads(output[json_start:])
    except json.JSONDecodeError:
        return []

    models: list[str] = []
    for entry in catalog.get("models", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("visibility", "list") != "list":
            continue
        slug = entry.get("slug")
        if isinstance(slug, str) and slug:
            models.append(slug)
    return _dedupe(models)


def _parse_model_output(output: str) -> list[str]:
    models: list[str] = []
    for line in output.splitlines():
        model = _strip_ansi(line).strip().lstrip("-*").strip()
        if not model or model.lower().startswith(("model", "name", "id ")):
            continue
        if _is_model_output_noise(model):
            continue
        if model.lower().startswith("default model:"):
            default_model = model.split(":", 1)[1].strip().split()
            if default_model:
                models.append(default_model[0])
            continue
        models.append(model.split()[0])
    return _dedupe(models)


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)


def _is_model_output_noise(line: str) -> bool:
    lower = line.lower()
    if lower.startswith(("available models:", "you are logged in", "error ", "warning:")):
        return True
    return " error " in lower or " warn " in lower


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique
