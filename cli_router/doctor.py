"""Doctor: detect and repair agent-CLI model-discovery drift.

Agent CLIs change their model catalogs and their output formats over time (for
example Codex retiring ``gpt-5``, or Grok reshuffling its ``grok models``
banner). Deterministic discovery in :mod:`cli_router.models` stays primary and
handles the common cases. Doctor is the fallback layer for when a provider's
CLI is installed and responding but its model list no longer parses.

The strategy is *LLM-as-parser*, not *LLM-as-operator*: Doctor runs the sick
provider's own list command itself, captures the raw text, and asks a working
agent to turn that raw text into a JSON array of model ids. Doctor never lets
the agent run arbitrary commands. Recovered lists are written to the
:class:`~cli_router.modelcache.ModelCache`, which layers over the static
defaults so later runs pick them up.

To stay resilient even when several agents are broken, Doctor does not trust a
single "healthy" provider. It builds an ordered chain of candidate *backends*
— ``(provider, model)`` pairs across every installed CLI (sorted, each model
from the cache/static list) — and walks it until one actually answers. The
first backend that works is pinned and reused for the remaining repairs. As
long as one provider+model combination works, Doctor can heal the rest. This
backend-selection seam (:func:`candidate_backends` + :func:`run_agent` +
``_BackendSelector``) is deliberately generic so future Doctor capabilities can
reuse it with a different task.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .modelcache import ModelCache
from .models import (
    DEFAULT_MODELS,
    MODEL_LIST_COMMANDS,
    PROVIDERS,
    CommandRunner,
    _looks_like_model,
    _parse_model_catalog,
    _provider_command,
    probe_models,
)

# Discovery during diagnosis is a deliberate setup/repair action, so it can wait
# longer than the snappy interactive picker for a slow CLI to cold-start.
DISCOVERY_TIMEOUT_SECONDS = 10.0
# The doctor agent may need to read a long, messy CLI dump and think about it.
DOCTOR_TIMEOUT_SECONDS = 120.0
SAFE_DOCTOR_PROVIDERS = frozenset({"codex", "claude", "grok"})

WhichFn = Callable[[str], str | None]
# (backend) -> recovered model ids; raises DoctorError when the backend fails.
BackendTask = Callable[["Backend"], list[str]]
Cancelled = Callable[[], bool]


class DoctorError(RuntimeError):
    """Raised when a doctor agent backend fails to produce a usable result."""


class DoctorCancelled(RuntimeError):
    """Raised when the user cancels an in-progress repair."""


@dataclass(frozen=True)
class Backend:
    """A single ``(provider, model)`` agent invocation the doctor can try."""

    provider: str
    model: str = ""

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model or 'default'}"

    def command(self, prompt: str) -> list[str]:
        if self.provider == "codex":
            return [
                "codex",
                "exec",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-rules",
                *(["-m", self.model] if self.model else []),
                prompt,
            ]
        if self.provider == "claude":
            return [
                "claude",
                "-p",
                *(["--model", self.model] if self.model else []),
                "--tools",
                "",
                "--permission-mode",
                "plan",
                "--no-session-persistence",
                "--safe-mode",
                prompt,
            ]
        if self.provider == "grok":
            return [
                "grok",
                *(["-m", self.model] if self.model else []),
                "--tools",
                "",
                "--permission-mode",
                "plan",
                "--no-memory",
                "--no-subagents",
                "--disable-web-search",
                "--max-turns",
                "1",
                "--single",
                prompt,
            ]
        raise DoctorError(f"{self.provider} has no tool-free doctor mode")


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    cli_present: bool
    healthy: bool
    models: list[str]
    source: str  # "catalog" | "text" | "cache" | "none"
    detail: str
    raw_output: str = ""
    command: list[str] | None = None

    @property
    def drifting(self) -> bool:
        """CLI is installed and responded, but nothing parsed — repairable."""
        return self.cli_present and not self.healthy and bool(self.raw_output.strip())


@dataclass
class RepairResult:
    provider: str
    ok: bool
    models: list[str] = field(default_factory=list)
    detail: str = ""
    doctor: str | None = None


def diagnose(
    providers: tuple[str, ...] = PROVIDERS,
    *,
    runner: CommandRunner | None = None,
    which: WhichFn | None = None,
    cache: ModelCache | None = None,
    discovery_timeout: float = DISCOVERY_TIMEOUT_SECONDS,
) -> list[ProviderHealth]:
    """Check each provider's model-discovery health.

    A provider is *healthy* when its CLI is installed and live discovery parses
    at least one model. When the CLI is present but nothing parses, we surface a
    cached list if we have one (still marked unhealthy, since live discovery is
    broken and should be repaired).
    """
    runner = runner or subprocess.run
    which = which or _default_which

    results: list[ProviderHealth] = []
    for provider in providers:
        executable = _executable(provider)
        if which(executable) is None:
            results.append(
                ProviderHealth(provider, False, False, [], "none", f"{executable} not installed")
            )
            continue

        if not MODEL_LIST_COMMANDS.get(provider):
            # Static-only provider (no safe discovery command) — not drift.
            static = (cache.get(provider) if cache is not None else []) or list(
                DEFAULT_MODELS.get(provider, [])
            )
            detail = (
                f"{len(static)} static models (no discovery command)"
                if static
                else "no discovery command and no static models"
            )
            results.append(
                ProviderHealth(provider, True, False, static, "static" if static else "none", detail)
            )
            continue

        probe = probe_models(provider, runner, timeout=discovery_timeout)
        if probe.models:
            source = "catalog" if _parse_model_catalog(probe.output) else "text"
            results.append(
                ProviderHealth(
                    provider,
                    True,
                    True,
                    probe.models,
                    source,
                    f"{len(probe.models)} models via {' '.join(probe.command or [])}",
                    probe.output,
                    probe.command,
                )
            )
            continue

        cached = cache.get(provider) if cache is not None else []
        detail = "model list did not parse"
        if not probe.ran:
            detail = "model-list command did not run"
        if cached:
            detail += f" (using {len(cached)} cached)"
        results.append(
            ProviderHealth(
                provider,
                True,
                False,
                cached,
                "cache" if cached else "none",
                detail,
                probe.output,
                probe.command,
            )
        )
    return results


def candidate_backends(
    providers: tuple[str, ...] = PROVIDERS,
    *,
    cache: ModelCache | None = None,
    which: WhichFn | None = None,
) -> list[Backend]:
    """Build the ordered failover chain of agent backends to try.

    Providers are tried alphabetically; within a provider, cache-known models
    come first (freshest), then the static ``DEFAULT_MODELS``. A provider whose
    CLI is not installed is skipped entirely. Trying a stale model simply fails
    and falls through to the next candidate — which is the whole point.
    """
    which = which or _default_which
    backends: list[Backend] = []
    for provider in sorted(providers):
        if provider not in SAFE_DOCTOR_PROVIDERS:
            continue
        if which(_executable(provider)) is None:
            continue
        models = list(cache.get(provider)) if cache is not None else []
        models += list(DEFAULT_MODELS.get(provider, []))
        seen: set[str] = set()
        ordered: list[str] = []
        for model in models:
            if model not in seen:
                seen.add(model)
                ordered.append(model)
        for model in ordered or [""]:
            backends.append(Backend(provider, model))
    return backends


def repair(
    health: list[ProviderHealth],
    *,
    cache: ModelCache,
    backends: Iterable[Backend] | None = None,
    runner: CommandRunner | None = None,
    cancelled: Cancelled | None = None,
) -> list[RepairResult]:
    """Repair drifting providers by walking the backend chain until one works.

    Recovered lists are written to ``cache`` (saved once at the end). Returns a
    report per drifting provider — including failures — so callers can surface
    what could not be fixed. Cancellation (via the ``cancelled`` hook or Ctrl-C)
    stops gracefully, preserving whatever was already recovered.
    """
    runner = runner or subprocess.run
    drifting = [h for h in health if h.drifting]
    if not drifting:
        return []

    chain = list(backends) if backends is not None else candidate_backends(cache=cache)
    if not chain:
        return [
            RepairResult(h.provider, False, detail="no agent CLI available to act as doctor")
            for h in drifting
        ]

    selector = _BackendSelector(chain)
    reports: list[RepairResult] = []
    for h in drifting:
        def task(backend: Backend, health: ProviderHealth = h) -> list[str]:
            return _extract_models_for(backend, health, runner)

        try:
            backend, models = selector.run(task, cancelled)
        except (DoctorCancelled, KeyboardInterrupt):
            reports.append(RepairResult(h.provider, False, detail="cancelled by user"))
            break

        if backend is None:
            reports.append(
                RepairResult(h.provider, False, detail="no working agent backend could read the model list")
            )
            continue

        usable = [model for model in _clean(models) if _looks_like_model(model)]
        if usable:
            cache.set(h.provider, usable)
            reports.append(
                RepairResult(
                    h.provider,
                    True,
                    usable,
                    f"recovered {len(usable)} models via {backend.label}",
                    backend.provider,
                )
            )
        else:
            reports.append(
                RepairResult(
                    h.provider,
                    False,
                    detail=f"{backend.label} returned no usable model ids",
                    doctor=backend.provider,
                )
            )

    if any(report.ok for report in reports):
        cache.save()
    return reports


class _BackendSelector:
    """Walks the backend chain, pinning the first that works and reusing it."""

    def __init__(self, backends: list[Backend]) -> None:
        self._backends = backends
        self._pinned: Backend | None = None

    def run(self, task: BackendTask, cancelled: Cancelled | None) -> tuple[Backend | None, list[str]]:
        order = ([self._pinned] if self._pinned else []) + [
            backend for backend in self._backends if backend is not self._pinned
        ]
        for backend in order:
            if cancelled is not None and cancelled():
                raise DoctorCancelled()
            try:
                result = task(backend)
            except DoctorError:
                if backend is self._pinned:
                    self._pinned = None  # the pinned backend went bad; keep trying others
                continue
            self._pinned = backend
            return backend, result
        return None, []


_EXTRACT_PROMPT = (
    "The shell command `{command}` lists the models an AI coding CLI supports, "
    "but its output format changed and an automated parser could not read it. "
    "Between the <<<RAW>>> markers below is the exact raw output.\n\n"
    "Return ONLY a compact JSON array of the model id strings a user could "
    'select (for example ["gpt-5.6-sol","gpt-5.5"]). Output no prose, no '
    "markdown code fences, and no explanation — just the JSON array.\n\n"
    "<<<RAW>>>\n{raw}\n<<<RAW>>>"
)


def _extract_models_for(backend: Backend, health: ProviderHealth, runner: CommandRunner) -> list[str]:
    prompt = _EXTRACT_PROMPT.format(
        command=" ".join(health.command or []) or f"{health.provider} models",
        raw=health.raw_output,
    )
    text = run_agent(backend, prompt, runner)
    models = extract_json_array(text)
    if not models:
        raise DoctorError(f"{backend.label} returned no JSON model array")
    return models


def run_agent(
    backend: Backend,
    prompt: str,
    runner: CommandRunner,
    *,
    timeout: float = DOCTOR_TIMEOUT_SECONDS,
) -> str:
    """Run one agent backend in plain-text mode and return its stdout.

    This is the modular LLM seam: everything above it is provider-agnostic and
    reusable by future Doctor tasks. Raises :class:`DoctorError` on any failure
    so the selector can fail over to the next candidate.
    """
    argv = backend.command(prompt)
    env = os.environ.copy()
    for name in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
    ):
        env.pop(name, None)
    try:
        with tempfile.TemporaryDirectory(prefix="cli-router-doctor-") as working_dir:
            completed = runner(
                argv,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                cwd=working_dir,
                env=env,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DoctorError(f"{backend.label} did not run: {exc}") from exc
    if completed.returncode != 0:
        raise DoctorError(
            f"{backend.label} exited {completed.returncode}: {(completed.stderr or '').strip()[:200]}"
        )
    return completed.stdout or ""


def _executable(provider: str) -> str:
    return _provider_command(provider)[0]


def _default_which(executable: str) -> str | None:
    import shutil

    return shutil.which(executable)


def _clean(models: list[str]) -> list[str]:
    return list(dict.fromkeys(model.strip() for model in models if isinstance(model, str) and model.strip()))


def extract_json_array(text: str) -> list[str]:
    """Pull a JSON array of strings out of an agent's stdout.

    Tolerant of surrounding prose, code fences, and common object wrappers like
    ``{"models": [...]}`` or ``{"result": "[...]"}`` — model CLIs vary in how
    much they wrap their answer, so we scan candidates rather than assume one.
    """
    for candidate in _json_candidates(text):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        found = _strings_from_json(value)
        if found:
            return found
    return []


def _strings_from_json(value: object) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, dict):
        for key in ("models", "result", "output", "data"):
            if key in value:
                nested = value[key]
                if isinstance(nested, str):
                    inner = extract_json_array(nested)
                    if inner:
                        return inner
                else:
                    inner = _strings_from_json(nested)
                    if inner:
                        return inner
    return []


def _json_candidates(text: str) -> Iterable[str]:
    stripped = text.strip()
    if stripped:
        yield stripped
    # Flat arrays of strings contain no nested brackets, so a non-greedy
    # bracket match is enough and avoids grabbing an entire noisy transcript.
    for match in re.finditer(r"\[[^\[\]]*\]", text, re.DOTALL):
        yield match.group(0)
    for match in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        yield match.group(0)
