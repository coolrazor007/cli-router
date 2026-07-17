"""Subprocess runner for configured external tools."""

from __future__ import annotations

import os
import re
import shlex
import signal
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
        process = subprocess.Popen(
            rendered,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_process_group_options(),
        )
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        stdout, stderr = process.communicate()
        stderr += f"Command timed out after {timeout_seconds:g} seconds\n"
        return ToolRunResult(rendered, 124, stdout, stderr, _elapsed(started))
    except FileNotFoundError:
        return ToolRunResult(rendered, 127, "", f"Command not found: {rendered[0]}\n", _elapsed(started))
    except OSError as exc:
        return ToolRunResult(rendered, 126, "", f"Failed to run command: {exc}\n", _elapsed(started))

    return ToolRunResult(rendered, process.returncode or 0, stdout, stderr, _elapsed(started))


def stream_tool(
    tool: Mapping[str, Any],
    variables: Mapping[str, Any],
    *,
    on_stdout_line: Callable[[str], None] | None = None,
    on_stderr_line: Callable[[str], None] | None = None,
) -> ToolRunResult:
    """Run a tool while delivering stdout/stderr lines as they are observed.

    Both streams are funnelled through one tagged queue and dispatched from this
    (single) loop, so ``on_stdout_line``/``on_stderr_line`` are always invoked
    from the same thread — callers can render to a live view without their own
    locking. Within each stream, line order is preserved.
    """

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
            **_process_group_options(),
        )
    except FileNotFoundError:
        return ToolRunResult(rendered, 127, "", f"Command not found: {rendered[0]}\n", _elapsed(started))
    except OSError as exc:
        return ToolRunResult(rendered, 126, "", f"Failed to run command: {exc}\n", _elapsed(started))

    output_queue: Queue[tuple[str, str]] = Queue()
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def read_stream(pipe: Any, name: str) -> None:
        for line in pipe:
            output_queue.put((name, line))

    stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, "stdout"), daemon=True)
    stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, "stderr"), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    def dispatch(name: str, line: str) -> None:
        if name == "stdout":
            stdout_lines.append(line)
            if on_stdout_line is not None:
                on_stdout_line(line)
        else:
            stderr_lines.append(line)
            if on_stderr_line is not None:
                on_stderr_line(line)

    timed_out = False
    while (
        process.poll() is None
        or stdout_thread.is_alive()
        or stderr_thread.is_alive()
        or not output_queue.empty()
    ):
        if (
            timeout_seconds is not None
            and process.poll() is None
            and time.monotonic() - started >= timeout_seconds
        ):
            timed_out = True
            _kill_process_tree(process)
            break
        try:
            name, line = output_queue.get(timeout=0.05)
        except Empty:
            continue
        dispatch(name, line)

    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    while not output_queue.empty():
        name, line = output_queue.get()
        dispatch(name, line)

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


def _process_group_options() -> dict[str, Any]:
    if os.name == "posix":
        return {"start_new_session": True}
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {}


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif os.name == "nt" and process.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    if process.poll() is None:
        process.kill()
    process.wait()


def _elapsed(started: float) -> float:
    return max(0.0, time.monotonic() - started)
