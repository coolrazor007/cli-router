import sys
import time

from cli_router.runner import run_tool


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


def test_runner_reports_missing_command_as_nonzero_result():
    result = run_tool({"command": ["definitely-not-a-cli-router-command"]}, {})

    assert result.returncode == 127
    assert "not found" in result.stderr.lower()


def test_runner_reports_timeout_as_nonzero_result():
    started = time.monotonic()

    result = run_tool(
        {"command": [sys.executable, "-c", "import time; time.sleep(10)"], "timeout_seconds": 0.1},
        {},
    )

    assert time.monotonic() - started < 5
    assert result.returncode == 124
    assert "timed out" in result.stderr.lower()
