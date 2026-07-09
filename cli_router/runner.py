"""Subprocess runner for configured external tools."""

from __future__ import annotations

import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from queue import Empty, Queue
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ToolRunResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float = 0.0


def run_tool(tool: Mapping[str, Any], variables: Mapping[str, Any]) -> ToolRunResult:
    started = time.monotonic()
    rendered = _render_command(tool, variables)
    if not rendered:
        return ToolRunResult([], 2, "", "Tool is missing a command\n", _elapsed(started))
    timeout_seconds = _timeout_seconds(tool.get("timeout_seconds"))

    try:
        completed = subprocess.run(
            rendered,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        stderr += f"Command timed out after {timeout_seconds:g} seconds\n"
        return ToolRunResult(rendered, 124, stdout, stderr, _elapsed(started))
    except FileNotFoundError:
        return ToolRunResult(rendered, 127, "", f"Command not found: {rendered[0]}\n", _elapsed(started))
    except OSError as exc:
        return ToolRunResult(rendered, 126, "", f"Failed to run command: {exc}\n", _elapsed(started))

    return ToolRunResult(rendered, completed.returncode, completed.stdout, completed.stderr, _elapsed(started))


def stream_tool(
    tool: Mapping[str, Any],
    variables: Mapping[str, Any],
    *,
    on_stdout_line: Callable[[str], None] | None = None,
) -> ToolRunResult:
    """Run a tool while delivering stdout lines as they are observed."""

    started = time.monotonic()
    rendered = _render_command(tool, variables)
    if not rendered:
        return ToolRunResult([], 2, "", "Tool is missing a command\n", _elapsed(started))
    timeout_seconds = _timeout_seconds(tool.get("timeout_seconds"))

    try:
        process = subprocess.Popen(
            rendered,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        return ToolRunResult(rendered, 127, "", f"Command not found: {rendered[0]}\n", _elapsed(started))
    except OSError as exc:
        return ToolRunResult(rendered, 126, "", f"Failed to run command: {exc}\n", _elapsed(started))

    stdout_queue: Queue[str] = Queue()
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_queue.put(line)

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_lines.append(line)

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    stdout_lines: list[str] = []
    timed_out = False

    while process.poll() is None or stdout_thread.is_alive() or not stdout_queue.empty():
        try:
            line = stdout_queue.get(timeout=0.05)
        except Empty:
            if timeout_seconds is not None and time.monotonic() - started >= timeout_seconds:
                timed_out = True
                process.kill()
                break
            continue
        stdout_lines.append(line)
        if on_stdout_line is not None:
            on_stdout_line(line)

    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    while not stdout_queue.empty():
        line = stdout_queue.get()
        stdout_lines.append(line)
        if on_stdout_line is not None:
            on_stdout_line(line)

    if timed_out:
        stderr = "".join(stderr_lines)
        stderr += f"Command timed out after {timeout_seconds:g} seconds\n"
        return ToolRunResult(rendered, 124, "".join(stdout_lines), stderr, _elapsed(started))

    return ToolRunResult(rendered, process.returncode or 0, "".join(stdout_lines), "".join(stderr_lines), _elapsed(started))


def _render_command(tool: Mapping[str, Any], variables: Mapping[str, Any]) -> list[str]:
    raw_command = tool.get("command")
    if not raw_command:
        return []
    command = _normalize_command(raw_command)
    return [_render_arg(arg, variables) for arg in command]


def _normalize_command(raw_command: Any) -> list[str]:
    if isinstance(raw_command, str):
        return shlex.split(raw_command)
    if isinstance(raw_command, list) and all(isinstance(item, str) for item in raw_command):
        return raw_command
    return []


@lru_cache(maxsize=128)
def _placeholder_pattern(keys: tuple[str, ...]) -> re.Pattern[str]:
    """Compile (and cache) the alternation matching ``{key}`` for each key.

    The pattern depends only on the placeholder names, not their values, so it
    is safe to reuse across calls. ``run_tool`` renders every command argument
    with the same variable set, and workflows render many stages with a fixed
    set of names, so caching avoids recompiling the same regex repeatedly.
    """
    return re.compile("|".join(re.escape("{" + key + "}") for key in keys))


def render_placeholders(text: str, variables: Mapping[str, Any]) -> str:
    """Replace every ``{key}`` placeholder in a single left-to-right pass.

    Substituting one placeholder at a time with ``str.replace`` re-scans the
    already-substituted text, so a value that happens to contain another
    placeholder token (for example a user prompt containing ``{plan_path}``)
    would be expanded a second time. A single regex pass over the original
    string avoids that by never re-examining substituted content. Unknown
    placeholders are left untouched.
    """
    if not variables:
        return text
    pattern = _placeholder_pattern(tuple(variables))
    return pattern.sub(lambda match: str(variables[match.group(0)[1:-1]]), text)


def _render_arg(arg: str, variables: Mapping[str, Any]) -> str:
    return render_placeholders(arg, variables)


def _timeout_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return None
    return timeout if timeout > 0 else None


def _elapsed(started: float) -> float:
    return max(0.0, time.monotonic() - started)
