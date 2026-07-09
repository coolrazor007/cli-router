import sys
import time

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
