import os
import sys
import threading
import tty
from pathlib import Path

import yaml
from rich.console import Console

from cli_router.config import load_config, user_config_path
from cli_router.tui import (
    ModelConfigOption,
    PromptVariableError,
    add_session_stage,
    model_configs_for_config,
    move_stage,
    normalize_prompt_template,
    prompt_preview,
    _read_escape_sequence,
    _read_posix_key,
    run_tui,
    selected_stage_names,
    set_stage_model_config,
    stage_options_for_workflow,
    toggle_selection,
    update_model_config,
)


def test_posix_key_reader_returns_bare_escape_without_blocking(monkeypatch):
    master_fd, slave_fd = os.openpty()
    result = []
    errors = []

    tty.setraw(slave_fd)
    with os.fdopen(slave_fd, "r", encoding="utf-8", buffering=1) as slave:
        monkeypatch.setattr(sys, "stdin", slave)

        def read_key():
            try:
                result.append(_read_posix_key())
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=read_key)
        thread.start()
        try:
            os.write(master_fd, b"\x1b")
            thread.join(timeout=0.2)
            assert not thread.is_alive()
            assert errors == []
            assert result == ["\x1b"]
        finally:
            if thread.is_alive():
                os.write(master_fd, b"xx")
                thread.join(timeout=1)
            os.close(master_fd)


def test_escape_sequence_reader_drains_full_sequence():
    read_fd, write_fd = os.pipe()

    try:
        os.write(write_fd, b"[1;5A")
        read_char = lambda: os.read(read_fd, 1).decode("utf-8")
        assert _read_escape_sequence(read_fd, read_char, "\x1b") == "\x1b[1;5A"
    finally:
        os.close(read_fd)
        os.close(write_fd)


def write_stage_config(tmp_path):
    Path(tmp_path, "cli-router.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "defaults": {"run_dir": ".cli-router/runs"},
                "tools": {
                    "alpha": {
                        "provider": "openai",
                        "model": "gpt-5.1",
                        "effort": "medium",
                        "command": [sys.executable, "-c", "print('alpha')"],
                        "output": {"format": "text"},
                    },
                    "beta": {
                        "command": [sys.executable, "-c", "print('beta')"],
                        "output": {"format": "text"},
                    },
                },
                "workflows": {
                    "default": {
                        "stages": [
                            {"id": "alpha", "tool": "alpha", "input_template": "Plan this: {user_prompt}"},
                            {
                                "id": "beta",
                                "tool": "beta",
                                "enabled": False,
                                "input_template": "Please implement this: {plan_path}",
                            },
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_stage_options_use_configured_enabled_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)

    options = stage_options_for_workflow(load_config(), "default")

    assert [(option.stage_id, option.tool, option.selected) for option in options] == [
        ("alpha", "alpha", True),
        ("beta", "beta", False),
    ]


def test_toggle_selection_changes_selected_stage_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    options = stage_options_for_workflow(load_config(), "default")

    selected = toggle_selection([option.selected for option in options], 1)

    assert selected_stage_names(options, selected) == ["alpha", "beta"]


def test_move_stage_changes_selected_run_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    options = stage_options_for_workflow(load_config(), "default")
    selected = [True, True]

    moved_options, moved_selected, cursor = move_stage(options, selected, 1, -1)

    assert cursor == 0
    assert selected_stage_names(moved_options, moved_selected) == ["beta", "alpha"]


def test_tui_runs_checked_stages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    keys = iter(["\x1b[B", "\r", "\x1b[B", " ", "\r"])
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)

    exit_code = run_tui(load_config(), workflow_name="default", prompt="ignored", console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    run_dirs = list(Path(".cli-router/runs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "alpha.stdout").read_text(encoding="utf-8") == "alpha\n"
    assert (run_dirs[0] / "beta.stdout").read_text(encoding="utf-8") == "beta\n"


def test_tui_without_prompt_starts_menu_before_prompting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)

    def fail_if_prompted(*args, **kwargs):
        raise AssertionError("TUI should not prompt before showing the menu")

    monkeypatch.setattr(console, "input", fail_if_prompted)

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: "q")

    assert exit_code == 0


def test_tui_starts_with_main_menu(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    output_path = Path(tmp_path, "tui.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: "q")

    assert exit_code == 0
    output = output_path.read_text(encoding="utf-8")
    assert output.index("Prompt") < output.index("Workflow")
    assert "Workflow" in output
    assert "Prompt" in output
    assert "Stage configuration" in output
    assert "Model Config" in output


def test_tui_workflow_back_returns_to_main_menu(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    output_path = Path(tmp_path, "tui.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\r", "b", "q"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    output = output_path.read_text(encoding="utf-8")
    assert output.count("CLI-Router") >= 3
    assert "B back" in output
    assert "Q quit" in output
    assert "q back" not in output


def test_tui_workflow_renders_unicode_checkbox_selector_and_prompt_column(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    output_path = Path(tmp_path, "tui.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\r", "q", "q"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    output = output_path.read_text(encoding="utf-8")
    assert "☑" in output
    assert "☐" in output
    assert "[x]" not in output
    assert "[ ]" not in output
    workflow_output = output[output.index("CLI-Router: default") :]
    assert workflow_output.index("Run") < workflow_output.index("Stage")
    assert "Prompt" in output
    assert "State" not in output
    assert "Please implement this: [previous stage" in output
    assert "output]" in output
    workflow_output = output[output.index("CLI-Router: default") :]
    assert "\nPrompt:" not in workflow_output


def test_prompt_preview_shows_previous_stage_input_marker():
    assert prompt_preview("Please implement this: {plan_path}", "") == "Please implement this: [previous stage output]"


def test_prompt_variables_are_normalized_and_enforced():
    assert normalize_prompt_template("Plan [user prompt]") == "Plan {user_prompt}"
    assert normalize_prompt_template("Implement [previous stage output]") == "Implement {plan_path}"

    try:
        normalize_prompt_template("Use [unknown thing]")
    except PromptVariableError as exc:
        assert "[unknown thing]" in str(exc)
    else:
        raise AssertionError("unknown prompt variable should fail")


def test_tui_prompt_menu_prompts_and_runs_enabled_workflow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\r", *"run from prompt menu", "\r"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    run_dirs = list(Path(".cli-router/runs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "alpha.stdout").read_text(encoding="utf-8") == "alpha\n"
    assert not (run_dirs[0] / "beta.stdout").exists()
    run_manifest = (run_dirs[0] / "run.yaml").read_text(encoding="utf-8")
    assert "user_prompt: run from prompt menu" in run_manifest
    output = Path(tmp_path, "tui.txt").read_text(encoding="utf-8")
    assert "Running workflow" in output
    assert "run from prompt menu" in output
    assert "alpha" in output
    assert "Full output saved to" in output
    assert "stdout:" not in output


def test_tui_prompt_menu_full_verbosity_keeps_raw_output_dump(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    config.defaults["tui_verbosity"] = "full"
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\r", *"run from prompt menu", "\r"])

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    output = Path(tmp_path, "tui.txt").read_text(encoding="utf-8")
    assert "stdout:" in output
    assert "alpha\n" in output


def test_add_session_stage_uses_first_configured_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()

    add_session_stage(config, "default", "review", "Review this: [previous stage output]")

    stage = config.workflows["default"]["stages"][-1]
    assert stage == {
        "id": "review",
        "tool": "alpha",
        "input_template": "Review this: {plan_path}",
        "enabled": True,
    }


def test_add_session_stage_can_link_model_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()

    add_session_stage(config, "default", "review", "Review [previous stage output]", model_config="beta")

    stage = config.workflows["default"]["stages"][-1]
    assert stage["tool"] == "beta"


def test_set_stage_model_config_updates_stage_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()

    set_stage_model_config(config, "default", "alpha", "beta")

    assert config.workflows["default"]["stages"][0]["tool"] == "beta"


def test_stage_configuration_edits_selected_stage_with_model_config_picker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\x1b[B", "\r", "\x1b[B", "\r", *"Updated [user prompt]", "\r", "\x1b[A", "\r", "q"])

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    stage = config.workflows["default"]["stages"][1]
    assert stage["tool"] == "alpha"
    assert stage["input_template"] == "Updated {user_prompt}"


def test_stage_configuration_model_config_change_uses_selected_stage_and_picker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)

    def fail_if_prompted(*args, **kwargs):
        raise AssertionError("Model config changes should use a picker, not typed stage/model names")

    monkeypatch.setattr(console, "input", fail_if_prompted)
    keys = iter(["\x1b[B", "\x1b[B", "\r", "\x1b[B", "m", "\x1b[A", "\r", "q"])

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    assert config.workflows["default"]["stages"][1]["tool"] == "alpha"


def test_stage_configuration_escape_cancels_selected_stage_edit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)

    def fail_if_prompted(*args, **kwargs):
        raise AssertionError("Stage editing should use TUI input so Esc can cancel")

    monkeypatch.setattr(console, "input", fail_if_prompted)
    keys = iter(["\x1b[B", "\x1b[B", "\r", "\r", "\x1b", "q"])

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    stage = config.workflows["default"]["stages"][0]
    assert stage["tool"] == "alpha"
    assert stage["input_template"] == "Plan this: {user_prompt}"


def test_stage_configuration_escape_cancels_add_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)

    def fail_if_prompted(*args, **kwargs):
        raise AssertionError("Stage add should use TUI input so Esc can cancel")

    monkeypatch.setattr(console, "input", fail_if_prompted)
    keys = iter(["\x1b[B", "\x1b[B", "\r", "a", "\x1b", "q"])

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    assert [stage["id"] for stage in config.workflows["default"]["stages"]] == ["alpha", "beta"]


def test_stage_configuration_displays_clear_add_instruction_and_variables(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    output_path = Path(tmp_path, "tui.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\x1b[B", "\r", "q"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    output = output_path.read_text(encoding="utf-8")
    assert "Press A to add a stage" in output
    assert "Press M to change model config" in output
    assert "Press B to go back" in output
    assert "Press Q to quit" in output
    assert "Model Config" in output
    assert "Tool" not in output
    assert "[user prompt]" in output
    assert "[previous stage output]" in output
    assert "a add" not in output


def test_model_config_options_read_provider_model_effort(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)

    options = model_configs_for_config(load_config())

    assert ModelConfigOption("alpha", "openai", "gpt-5.1", "medium") in options


def test_update_model_config_sets_provider_model_effort(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()

    update_model_config(config, "alpha", provider="anthropic", model="claude-sonnet-4.5", effort="high")

    assert config.tools["alpha"]["provider"] == "anthropic"
    assert config.tools["alpha"]["model"] == "claude-sonnet-4.5"
    assert config.tools["alpha"]["effort"] == "high"


def test_model_config_screen_is_editable_and_hides_command_and_type(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    output_path = Path(tmp_path, "tui.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\x1b[B", "\x1b[B", "\r", "e", "\x1b[B", "\r", "\r", "\x1b[B", "\r", "q"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    output = output_path.read_text(encoding="utf-8")
    assert "Model Config" in output
    assert "Provider" in output
    assert "Model" in output
    assert "Effort" in output
    assert "Command" not in output
    assert "Type" not in output
    assert "Press E to edit model config" in output


def test_model_config_enter_does_not_start_edit_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    output_path = Path(tmp_path, "tui.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)

    def fail_if_prompted(*args, **kwargs):
        raise AssertionError("Enter should not start model config editing")

    monkeypatch.setattr(console, "input", fail_if_prompted)
    keys = iter(["\x1b[B", "\x1b[B", "\x1b[B", "\r", "\r", "q"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0


def test_ctrl_c_exits_from_model_config_picker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    output_path = Path(tmp_path, "tui.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\x1b[B", "\x1b[B", "\r", "e", "\x03"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 130
    output = output_path.read_text(encoding="utf-8")
    assert "Canceled" in output


def test_ctrl_c_exits_from_prompt_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    output_path = Path(tmp_path, "tui.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\r", "\x03"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 130
    output = output_path.read_text(encoding="utf-8")
    assert "Canceled" in output


def test_first_run_selects_provider_and_persists_user_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    output_path = Path(tmp_path, "tui.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter([" ", "\r", "q"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    saved = user_config_path()
    assert saved.exists()
    saved_text = saved.read_text(encoding="utf-8")
    assert "provider: codex" in saved_text
    assert "tool: codex" in saved_text
    output = output_path.read_text(encoding="utf-8")
    assert "codex" in output
    assert "claude" in output
    assert "hermes" in output


def test_tui_stage_add_persists_to_home_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".cli-router" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "tools": {
                    "codex": {
                        "provider": "codex",
                        "model": "gpt-5.1",
                        "effort": "medium",
                        "command": ["codex", "exec", "{prompt}"],
                    }
                },
                "workflows": {
                    "default": {
                        "stages": [
                            {"id": "planner", "tool": "codex", "input_template": "Plan [user prompt]"}
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\x1b[B", "\r", "a", *"review", "\r", *"Review [previous stage output]", "\r", "\r", "q"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    saved = config_path.read_text(encoding="utf-8")
    assert "id: review" in saved
    assert "input_template: Review {plan_path}" in saved
