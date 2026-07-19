import os
import sys
import time

import pytest

from cli_router.runner import _placeholder_pattern, render_placeholders, run_tool, stream_tool


def test_render_placeholders_does_not_reexpand_substituted_values():
    rendered = render_placeholders(
        "{prompt}",
        {"prompt": "use {plan_path} carefully", "plan_path": "PLAN.md"},
    )

    assert rendered == "use {plan_path} carefully"


def test_render_placeholders_leaves_unknown_tokens_intact():
    assert render_placeholders("{prompt} and {unknown}", {"prompt": "hi"}) == "hi and {unknown}"


def test_render_placeholders_reuses_compiled_pattern_for_same_keys():
    _placeholder_pattern.cache_clear()

    render_placeholders("{prompt} {plan_path}", {"prompt": "a", "plan_path": "b"})
    render_placeholders("only {prompt}", {"prompt": "c", "plan_path": "d"})

    info = _placeholder_pattern.cache_info()
    assert info.misses == 1
    assert info.hits == 1


def test_runner_renders_prompt_placeholder_and_captures_streams():
    result = run_tool(
        {
            "command": [
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1]); print('warn', file=sys.stderr)",
                "{prompt}",
            ]
        },
        {"prompt": "hello"},
    )

    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert result.stderr == "warn\n"
    assert result.duration_seconds >= 0


def test_runner_reports_missing_command_as_nonzero_result():
    result = run_tool({"command": ["definitely-not-a-cli-router-command"]}, {})

    assert result.returncode == 127
    assert "not found" in result.stderr.lower()
    assert result.duration_seconds >= 0


def test_runner_reports_timeout_as_nonzero_result():
    started = time.monotonic()

    result = run_tool(
        {"command": [sys.executable, "-c", "import time; time.sleep(10)"], "timeout_seconds": 0.1},
        {},
    )

    assert time.monotonic() - started < 5
    assert result.returncode == 124
    assert "timed out" in result.stderr.lower()
    assert result.duration_seconds >= 0.1


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
@pytest.mark.parametrize("runner", [run_tool, stream_tool])
def test_timeout_kills_descendant_processes(tmp_path, runner):
    marker = tmp_path / "descendant-finished"
    child = (
        "import time; from pathlib import Path; "
        f"time.sleep(0.4); Path({str(marker)!r}).write_text('survived')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(10)"
    )

    result = runner(
        {"command": [sys.executable, "-c", parent], "timeout_seconds": 0.1},
        {},
    )
    time.sleep(0.6)

    assert result.returncode == 124
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_timeout_kills_descendant_when_parent_has_already_exited(tmp_path):
    marker = tmp_path / "orphan-finished"
    child = (
        "import time; from pathlib import Path; "
        f"time.sleep(0.4); Path({str(marker)!r}).write_text('survived')"
    )
    parent = "import subprocess, sys; " f"subprocess.Popen([sys.executable, '-c', {child!r}])"

    result = run_tool(
        {"command": [sys.executable, "-c", parent], "timeout_seconds": 0.1},
        {},
    )

    assert result.returncode == 124
    assert not marker.exists()


def test_stream_tool_emits_stdout_lines_and_captures_full_result():
    lines = []

    result = stream_tool(
        {
            "command": [
                sys.executable,
                "-c",
                "import sys, time; print('one', flush=True); time.sleep(0.01); print('two', flush=True); print('warn', file=sys.stderr)",
            ]
        },
        {},
        on_stdout_line=lines.append,
    )

    assert lines == ["one\n", "two\n"]
    assert result.returncode == 0
    assert result.stdout == "one\ntwo\n"
    assert result.stderr == "warn\n"
    assert result.duration_seconds >= 0


def test_stream_tool_enforces_timeout_on_continuous_output():
    lines = []
    started = time.monotonic()

    result = stream_tool(
        {
            "command": [
                sys.executable,
                "-u",
                "-c",
                (
                    "import sys, time\n"
                    "deadline = time.monotonic() + 1\n"
                    "while time.monotonic() < deadline:\n"
                    "    sys.stdout.write('tick\\n')\n"
                    "    sys.stdout.flush()\n"
                ),
            ],
            "timeout_seconds": 0.2,
        },
        {},
        on_stdout_line=lines.append,
    )

    assert time.monotonic() - started < 5
    assert result.returncode == 124
    assert "timed out" in result.stderr.lower()
    assert result.duration_seconds >= 0.2
    assert lines


def test_stream_tool_delivers_stdout_and_stderr_to_separate_callbacks():
    out_lines = []
    err_lines = []

    result = stream_tool(
        {
            "command": [
                sys.executable,
                "-u",
                "-c",
                "import sys\nprint('answer')\nsys.stderr.write('progress\\n')\n",
            ],
        },
        {},
        on_stdout_line=out_lines.append,
        on_stderr_line=err_lines.append,
    )

    assert result.returncode == 0
    assert out_lines == ["answer\n"]
    assert err_lines == ["progress\n"]
    assert result.stdout == "answer\n"
    assert result.stderr == "progress\n"


def test_stream_tool_does_not_timeout_after_process_exits_while_draining_output():
    lines = []

    def slow_first_line(line):
        lines.append(line)
        if len(lines) == 1:
            time.sleep(0.3)

    result = stream_tool(
        {
            "command": [
                sys.executable,
                "-u",
                "-c",
                "import sys\nfor index in range(5):\n    print(f'line-{index}', flush=True)\n",
            ],
            "timeout_seconds": 0.2,
        },
        {},
        on_stdout_line=slow_first_line,
    )

    assert result.returncode == 0
    assert "timed out" not in result.stderr.lower()
    assert lines == [f"line-{index}\n" for index in range(5)]
    assert result.stdout == "".join(lines)


def test_runner_renders_configured_cwd(tmp_path):
    target_root = tmp_path / "target"
    working_dir = target_root / "review"
    working_dir.mkdir(parents=True)

    result = run_tool(
        {
            "command": [sys.executable, "-c", "import os; print(os.getcwd())"],
            "cwd": "{target_root}/review",
        },
        {"target_root": str(target_root)},
    )

    assert result.returncode == 0
    assert result.stdout.strip() == str(working_dir)


def test_runner_applies_environment_allowlist_overrides_and_unset(monkeypatch):
    monkeypatch.setenv("CLI_ROUTER_KEEP", "kept")
    monkeypatch.setenv("CLI_ROUTER_REMOVE", "remove-me")
    monkeypatch.setenv("CLI_ROUTER_HIDDEN", "must-not-leak")
    code = (
        "import json, os; "
        "print(json.dumps({key: os.environ.get(key) for key in "
        "['CLI_ROUTER_KEEP', 'CLI_ROUTER_REMOVE', 'CLI_ROUTER_HIDDEN', 'TERM', 'TARGET']}))"
    )

    result = run_tool(
        {
            "command": [sys.executable, "-c", code],
            "environment_mode": "allowlist",
            "environment_allowlist": ["CLI_ROUTER_KEEP", "CLI_ROUTER_REMOVE"],
            "environment": {"TERM": "dumb", "TARGET": "{target_root}"},
            "environment_unset": ["CLI_ROUTER_REMOVE"],
        },
        {"target_root": "/safe/target"},
    )

    assert result.returncode == 0
    assert result.stdout.strip() == (
        '{"CLI_ROUTER_KEEP": "kept", "CLI_ROUTER_REMOVE": null, '
        '"CLI_ROUTER_HIDDEN": null, "TERM": "dumb", "TARGET": "/safe/target"}'
    )


@pytest.mark.parametrize("runner", [run_tool, stream_tool])
def test_runner_closes_stdin_when_configured(runner):
    result = runner(
        {
            "command": [sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"],
            "stdin": "closed",
        },
        {},
    )

    assert result.returncode == 0
    assert result.stdout == "0\n"


def test_runner_redacts_environment_values_from_result_and_command(monkeypatch):
    secret = "router-secret-value"
    monkeypatch.setenv("CLI_ROUTER_SECRET", secret)
    code = (
        "import os, sys; "
        "print(sys.argv[1]); print(os.environ['CLI_ROUTER_SECRET']); "
        "print(os.environ['CLI_ROUTER_SECRET'], file=sys.stderr)"
    )

    result = run_tool(
        {
            "command": [sys.executable, "-c", code, "{secret_argument}"],
            "environment_mode": "allowlist",
            "environment_allowlist": ["CLI_ROUTER_SECRET"],
            "redact_environment_values": ["CLI_ROUTER_SECRET"],
        },
        {"secret_argument": secret},
    )

    assert secret not in " ".join(result.command)
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert result.command[-1] == "[REDACTED:CLI_ROUTER_SECRET]"
    assert result.stdout == "[REDACTED:CLI_ROUTER_SECRET]\n[REDACTED:CLI_ROUTER_SECRET]\n"
    assert result.stderr == "[REDACTED:CLI_ROUTER_SECRET]\n"


def test_stream_runner_redacts_values_before_callbacks(monkeypatch):
    secret = "stream-secret-value"
    monkeypatch.setenv("CLI_ROUTER_SECRET", secret)
    lines = []

    result = stream_tool(
        {
            "command": [sys.executable, "-c", "import os; print(os.environ['CLI_ROUTER_SECRET'])"],
            "redact_environment_values": ["CLI_ROUTER_SECRET"],
        },
        {},
        on_stdout_line=lines.append,
    )

    assert result.stdout == "[REDACTED:CLI_ROUTER_SECRET]\n"
    assert lines == ["[REDACTED:CLI_ROUTER_SECRET]\n"]


def test_runner_reports_missing_configured_cwd_as_configuration_error(tmp_path):
    result = run_tool(
        {
            "command": [sys.executable, "-c", "print('should not run')"],
            "cwd": str(tmp_path / "missing"),
        },
        {},
    )

    assert result.returncode == 2
    assert "configuration error" in result.stderr.lower()
