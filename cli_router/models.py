"""Provider and model helpers for TUI configuration."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .modelcache import ModelCache


PROVIDERS = ("codex", "claude", "hermes", "grok")

DEFAULT_MODELS = {
    "codex": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
    "claude": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
    "hermes": ["hermes-auto"],
    "grok": ["grok-build"],
}

# Only providers with a safe, non-interactive, machine-readable listing command
# belong here. Claude and Hermes have no such command — `claude models` hangs and
# `claude model list` would start a *billable agent turn*, while `hermes model` is
# an interactive login/selector — so they are intentionally omitted and fall back
# to the model cache / DEFAULT_MODELS instead of being probed.
MODEL_LIST_COMMANDS = {
    "codex": (["codex", "debug", "models"],),
    "grok": (["grok", "models"],),
}

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def model_options_for_provider(
    provider: str,
    runner: CommandRunner | None = None,
    *,
    cache: "ModelCache | None" = None,
) -> list[str]:
    runner = runner or subprocess.run
    discovered = _discover_models(provider, runner)
    if discovered:
        return discovered
    if cache is not None:
        cached = cache.get(provider)
        if cached:
            return cached
    return list(DEFAULT_MODELS.get(provider, ["default"]))


def provider_tool_config(provider: str, model: str, effort: str = "medium") -> dict[str, Any]:
    return {
        "type": provider,
        "provider": provider,
        "model": model,
        "effort": effort,
        "command": _provider_command(provider, model, effort),
        "output": {"format": "text"},
    }


def _provider_command(provider: str, model: str = "", effort: str = "") -> list[str]:
    """Build the CLI invocation for a provider, routing ``model`` and ``effort``.

    Both the selected model and reasoning effort have to reach the underlying
    CLI as actual arguments — otherwise every model config would run the tool's
    default model at its default effort. Each provider spells these differently
    (Codex takes effort as a ``-c`` config override, Claude/Grok as a flag), so
    that knowledge lives here rather than in the (provider-agnostic) runner.
    """
    if provider == "codex":
        return ["codex", "exec", *_effort_args(provider, effort), *_model_flag("-m", model), "{prompt}"]
    if provider == "claude":
        return ["claude", "-p", *_model_flag("--model", model), *_effort_args(provider, effort), "{prompt}"]
    if provider == "hermes":
        return ["hermes", "--oneshot", "{prompt}"]
    if provider == "grok":
        return ["grok", *_model_flag("-m", model), *_effort_args(provider, effort), "--single", "{prompt}"]
    return [provider, "{prompt}"]


def _model_flag(flag: str, model: str) -> list[str]:
    model = (model or "").strip()
    if not model or model == "default":
        return []
    return [flag, model]


def _effort_args(provider: str, effort: str) -> list[str]:
    effort = (effort or "").strip()
    if not effort or effort == "default":
        return []
    if provider == "codex":
        # Codex has no effort flag; it is a config override key.
        return ["-c", f"model_reasoning_effort={effort}"]
    if provider == "claude":
        return ["--effort", effort]
    if provider == "grok":
        return ["--reasoning-effort", effort]
    return []


@dataclass(frozen=True)
class DiscoveryProbe:
    """Result of probing a provider's model-list command(s).

    ``models`` is what the deterministic parsers extracted (possibly empty).
    ``output`` keeps the raw stdout of the command that ran so the doctor can
    hand it to an agent when parsing failed but the CLI clearly responded.
    """

    provider: str
    command: list[str] | None
    returncode: int | None
    output: str
    models: list[str]

    @property
    def ran(self) -> bool:
        return self.command is not None


def probe_models(provider: str, runner: CommandRunner, *, timeout: float = 1.5) -> DiscoveryProbe:
    last = DiscoveryProbe(provider, None, None, "", [])
    for command in MODEL_LIST_COMMANDS.get(provider, ()):
        try:
            completed = runner(
                command,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = completed.stdout or ""
        if completed.returncode != 0:
            last = DiscoveryProbe(provider, list(command), completed.returncode, output, [])
            continue
        models = _parse_model_catalog(output) or _parse_model_output(output)
        probe = DiscoveryProbe(provider, list(command), completed.returncode, output, models)
        if models:
            return probe
        last = probe
    return last


def _discover_models(provider: str, runner: CommandRunner) -> list[str]:
    return probe_models(provider, runner).models


def _parse_model_catalog(output: str) -> list[str]:
    # Scan each ``{`` and decode just the JSON object there, tolerating a banner
    # before it and any log line printed after it (drift the doctor exists for).
    decoder = json.JSONDecoder()
    index = output.find("{")
    while index != -1:
        try:
            catalog, _ = decoder.raw_decode(output, index)
        except json.JSONDecodeError:
            index = output.find("{", index + 1)
            continue
        if isinstance(catalog, dict) and isinstance(catalog.get("models"), list):
            models: list[str] = []
            for entry in catalog["models"]:
                if not isinstance(entry, dict):
                    continue
                if entry.get("visibility", "list") != "list":
                    continue
                slug = entry.get("slug")
                if isinstance(slug, str) and slug:
                    models.append(slug)
            return _dedupe(models)
        index = output.find("{", index + 1)
    return []


_AVAILABLE_HEADER = re.compile(r"(?i)^available models:")
_DEFAULT_MODEL = re.compile(r"(?i)^default model:\s*(.+)$")


def _parse_model_output(output: str) -> list[str]:
    """Extract model ids from a human-formatted ``... models`` CLI listing.

    Providers like Grok print a chatty banner (login status, fetch errors) to
    the same stream as the model list, so we cannot treat every non-empty line
    as a model. Instead we only trust two structured places — a ``Default
    model:`` line and the entries under an ``Available models:`` header — and
    only accept tokens that actually look like a model id. When the CLI emits a
    bare list with no headers (e.g. Claude), we fall back to scanning all lines,
    still gated on the model-id shape. That shape check is what keeps banner
    words like ``You`` out, regardless of how the surrounding prose drifts.
    """
    lines = [_strip_ansi(raw).strip() for raw in output.splitlines()]
    has_section = any(_AVAILABLE_HEADER.match(line) for line in lines)
    in_section = not has_section

    models: list[str] = []
    for line in lines:
        if _AVAILABLE_HEADER.match(line):
            in_section = True
            continue
        default = _DEFAULT_MODEL.match(line)
        if default:
            token = default.group(1).split()[0]
            if _looks_like_model(token):
                models.append(token)
            continue
        if not in_section or not line:
            continue
        tokens = line.lstrip("-*• \t").split()
        if tokens and _looks_like_model(tokens[0]):
            models.append(tokens[0])
    return _dedupe(models)


def _looks_like_model(token: str) -> bool:
    """A model id has no spaces and carries a version marker (a digit or ``-``).

    This rejects bare prose words (``You``, ``logged``, ``ERROR``) while
    accepting real slugs like ``grok-4.5``, ``gpt-5.6-sol``, ``hermes-auto``,
    and single-word versioned ids like ``o3``.
    """
    if not token or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", token):
        return False
    return any(char.isdigit() for char in token) or "-" in token


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique
