from cli_router.failures import classify_failure, stage_failure_message
from cli_router.runner import ToolRunResult


def test_classifies_usage_limit_from_stderr():
    result = ToolRunResult(["claude"], 1, "", "Claude AI usage limit reached|12345")

    assert classify_failure(result) == "usage_limit"
    assert "usage limit" in stage_failure_message("planner", result).lower()


def test_classifies_claude_session_limit_from_stdout():
    result = ToolRunResult(["claude"], 1, "You've hit your session limit · resets 2:30pm", "")

    assert classify_failure(result) == "usage_limit"


def test_classifies_nonzero_failure_as_command_failed():
    result = ToolRunResult(["tool"], 2, "", "bad")

    assert classify_failure(result) == "command_failed"


def test_classifies_timeout():
    result = ToolRunResult(["tool"], 124, "", "Command timed out after 1 seconds")

    assert classify_failure(result) == "timeout"


def test_classifies_unsupported_model():
    result = ToolRunResult(
        ["codex"],
        1,
        "",
        "The 'gpt-5' model is not supported when using Codex with a ChatGPT account.",
    )

    assert classify_failure(result) == "unsupported_model"


def test_stage_message_uses_precomputed_failure_kind():
    result = ToolRunResult(["claude"], 1, "", "boom")

    # A precomputed kind is trusted, so the caller's single classification is reused.
    message = stage_failure_message("planner", result, failure_kind="usage_limit")

    assert "usage limit" in message.lower()


def test_classifies_auth_required_from_provider_output():
    result = ToolRunResult(["claude"], 1, "Not logged in · Please run /login\n", "")

    assert classify_failure(result) == "auth_required"
    assert "authentication" in stage_failure_message("planner", result).lower()
    assert "Not logged in" in stage_failure_message("planner", result)


def test_classifies_transport_failure_from_provider_output():
    result = ToolRunResult(["grok"], 1, "", "connection reset by peer")

    assert classify_failure(result) == "transport_failure"


def test_classifies_invalid_runtime_policy_as_configuration_error():
    result = ToolRunResult(["tool"], 2, "", "Configuration error: cwd does not exist")

    assert classify_failure(result) == "configuration_error"
