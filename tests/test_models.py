import subprocess

from cli_router.models import model_options_for_provider, provider_tool_config


def test_model_options_use_provider_cli_when_available():
    def fake_runner(command, **kwargs):
        assert command == ["claude", "models"]
        return subprocess.CompletedProcess(command, 0, "claude-sonnet-4.5\nclaude-opus-4.1\n", "")

    assert model_options_for_provider("claude", runner=fake_runner) == ["claude-sonnet-4.5", "claude-opus-4.1"]


def test_codex_model_options_use_debug_catalog_json():
    calls = []

    def fake_runner(command, **kwargs):
        calls.append(command)
        assert command == ["codex", "debug", "models"]
        return subprocess.CompletedProcess(
            command,
            0,
            'WARNING: ignored\n{"models":[{"slug":"gpt-5.5","visibility":"list"},{"slug":"hidden-model","visibility":"hidden"},{"slug":"gpt-5.4","visibility":"list"}]}',
            "",
        )

    assert model_options_for_provider("codex", runner=fake_runner) == ["gpt-5.5", "gpt-5.4"]
    assert calls == [["codex", "debug", "models"]]


def test_model_options_fall_back_when_cli_is_unavailable():
    def missing_runner(command, **kwargs):
        raise FileNotFoundError(command[0])

    assert "gpt-5.1" in model_options_for_provider("codex", runner=missing_runner)


def test_claude_static_fallback_uses_current_claude_code_model_ids():
    def missing_runner(command, **kwargs):
        raise FileNotFoundError(command[0])

    assert model_options_for_provider("claude", runner=missing_runner) == [
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    ]


def test_model_discovery_closes_stdin_and_uses_short_timeout():
    calls = []

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "gpt-5.1\n", "")

    assert model_options_for_provider("codex", runner=fake_runner) == ["gpt-5.1"]
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1]["timeout"] <= 2


def test_model_discovery_falls_back_on_timeout():
    def timeout_runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    assert "claude-sonnet-5" in model_options_for_provider("claude", runner=timeout_runner)


def test_provider_tool_config_uses_provider_command_and_metadata():
    tool = provider_tool_config("claude", "claude-sonnet-4.5", "high")

    assert tool["provider"] == "claude"
    assert tool["model"] == "claude-sonnet-4.5"
    assert tool["effort"] == "high"
    assert tool["command"] == ["claude", "-p", "{prompt}"]
