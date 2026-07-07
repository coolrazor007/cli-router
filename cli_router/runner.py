"""Subprocess runner for configured external tools."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ToolRunResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_tool(tool: Mapping[str, Any], variables: Mapping[str, Any]) -> ToolRunResult:
    raw_command = tool.get("command")
    if not raw_command:
        return ToolRunResult([], 2, "", "Tool is missing a command\n")

    command = _normalize_command(raw_command)
    rendered = [_render_arg(arg, variables) for arg in command]
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
        return ToolRunResult(rendered, 124, stdout, stderr)
    except FileNotFoundError:
        return ToolRunResult(rendered, 127, "", f"Command not found: {rendered[0]}\n")
    except OSError as exc:
        return ToolRunResult(rendered, 126, "", f"Failed to run command: {exc}\n")

    return ToolRunResult(rendered, completed.returncode, completed.stdout, completed.stderr)


def _normalize_command(raw_command: Any) -> list[str]:
    if isinstance(raw_command, str):
        return shlex.split(raw_command)
    if isinstance(raw_command, list) and all(isinstance(item, str) for item in raw_command):
        return raw_command
    return []


def _render_arg(arg: str, variables: Mapping[str, Any]) -> str:
    rendered = arg
    for key, value in variables.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def _timeout_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return None
    return timeout if timeout > 0 else None
