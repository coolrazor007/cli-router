import os
import sys
import threading
import tty
from pathlib import Path

import yaml
from rich.console import Console

import cli_router.tui as tui
from cli_router.config import RouterConfig, load_config, user_config_path
from cli_router.tui import (
    ModelConfigOption,
    PromptVariableError,
    add_model_config,
    add_session_stage,
    insert_stage,
    model_configs_for_config,
    move_stage,
    move_workflow_stage,
    normalize_prompt_template,
    prompt_preview,
    remove_stage,
    _read_escape_sequence,
    _read_posix_key,
    _read_utf8_char,
    run_tui,
    selected_stage_names,
    set_stage_model_config,
    stage_library_options,
    stage_options_for_workflow,
    toggle_selection,
    unique_stage_id,
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


def _read_key_from_fd(fd):
    first = _read_utf8_char(fd)
    if first == "\x1b":
        return _read_escape_sequence(fd, lambda: os.read(fd, 1).decode("latin-1"), first)
    return first


def test_arrow_key_survives_single_burst_delivery():
    # Terminals deliver an arrow press as its whole escape sequence in one burst.
    # Reading via os.read (not a buffered TextIOWrapper) keeps select() and the
    # reads consistent so the sequence is not truncated to a bare Esc.
    cases = {
        b"\x1b[A": "\x1b[A",
        b"\x1b[B": "\x1b[B",
        b"\x1b[C": "\x1b[C",
        b"\x1b[D": "\x1b[D",
        b"\x1b": "\x1b",
        "é".encode(): "é",
        b"q": "q",
    }
    for raw, expected in cases.items():
        read_fd, write_fd = os.pipe()
        os.write(write_fd, raw)
        os.close(write_fd)
        try:
            assert _read_key_from_fd(read_fd) == expected
        finally:
            os.close(read_fd)


def test_escape_sequence_reader_drains_full_sequence():
    read_fd, write_fd = os.pipe()

    try:
        os.write(write_fd, b"[1;5A")
        read_char = lambda: os.read(read_fd, 1).decode("utf-8")
        assert _read_escape_sequence(read_fd, read_char, "\x1b") == "\x1b[1;5A"
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_posix_key_reader_returns_resize_marker_when_resize_is_pending(monkeypatch):
    master_fd, slave_fd = os.openpty()

    with os.fdopen(slave_fd, "r", encoding="utf-8", buffering=1) as slave:
        monkeypatch.setattr(sys, "stdin", slave)
        tui._request_resize_redraw()

        assert _read_posix_key() == tui.RESIZE_REDRAW_KEY
        assert not tui._consume_resize_redraw()

    os.close(master_fd)


def test_suspend_handler_restores_terminal_and_rerenders(monkeypatch):
    events = []
    handler = tui._TuiSignalHandlers(
        enabled=True,
        enter_screen=lambda: events.append("enter_screen"),
        leave_screen=lambda: events.append("leave_screen"),
        show_cursor=lambda visible: events.append(f"cursor:{visible}"),
    )
    monkeypatch.setattr(tui, "_restore_active_raw_terminal", lambda: events.append("restore_raw"))
    monkeypatch.setattr(tui, "_resume_active_raw_terminal", lambda: events.append("resume_raw"))
    monkeypatch.setattr(tui.os, "kill", lambda pid, sig: events.append(f"kill:{pid}:{sig}"))
    monkeypatch.setattr(tui.os, "getpid", lambda: 12345)

    handler._handle_suspend(None, None)

    assert events == [
        "restore_raw",
        "leave_screen",
        "cursor:True",
        f"kill:12345:{tui.signal.SIGTSTP}",
        "enter_screen",
        "cursor:False",
        "resume_raw",
    ]
    assert tui._consume_resize_redraw()


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
                                "input_template": "Please implement this: {previous_output}",
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


def test_workflow_screen_inserts_stage_from_library_with_unique_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    config.data["stage_library"] = [
        {"id": "alpha", "tool": "beta", "input_template": "Run [previous stage output]"}
    ]
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\r", "i", "\r", "q"])

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    assert [stage["id"] for stage in config.workflows["default"]["stages"]] == ["alpha", "alpha-2", "beta"]
    assert config.workflows["default"]["stages"][1]["input_template"] == "Run {previous_output}"


def test_workflow_screen_removes_selected_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\r", "x", "q"])

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    assert [stage["id"] for stage in config.workflows["default"]["stages"]] == ["beta"]


def test_tui_without_prompt_starts_menu_before_prompting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)

    def fail_if_prompted(*args, **kwargs):
        raise AssertionError("TUI should not prompt before showing the menu")

    monkeypatch.setattr(console, "input", fail_if_prompted)

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: "q")

    assert exit_code == 0


def test_tui_hides_and_restores_terminal_cursor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)

    class CursorSpyConsole(Console):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.cursor_visibility: list[bool] = []

        def show_cursor(self, show: bool = True) -> None:
            self.cursor_visibility.append(show)
            super().show_cursor(show)

    output_path = Path(tmp_path, "tui.txt")
    console = CursorSpyConsole(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: "q")

    assert exit_code == 0
    assert console.cursor_visibility == [False, True]


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


def test_tui_help_screen_is_generated_from_keymap(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    output_path = Path(tmp_path, "tui.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["?", "b", "q"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    output = output_path.read_text(encoding="utf-8")
    assert "Keyboard help" in output
    assert "Main" in output
    assert "Workflow" in output
    assert "B/Esc/Enter/? back" in output


def test_too_small_screen_reports_required_floor(tmp_path):
    output_path = Path(tmp_path, "small.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False, width=60, height=20)

    assert tui._terminal_too_small(console)
    tui._render_too_small(console)

    output = output_path.read_text(encoding="utf-8")
    assert "Terminal too small: 60x20" in output
    assert "Need at least 80x24" in output


def test_ascii_checkbox_fallback(monkeypatch):
    monkeypatch.setenv("CLI_ROUTER_TUI_ASCII", "1")

    assert tui._checkbox(True) == "[x]"
    assert tui._checkbox(False) == "[ ]"


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
    assert "B/Esc back" in output
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
    assert prompt_preview("Please implement this: {previous_output}", "") == "Please implement this: [previous stage output]"
    assert prompt_preview("See {all_stage_outputs}", "") == "See [all stage outputs]"
    assert prompt_preview("Plan in {plan_path}", "") == "Plan in [plan file]"


def test_prompt_variables_are_normalized_and_enforced():
    assert normalize_prompt_template("Plan [user prompt]") == "Plan {user_prompt}"
    assert normalize_prompt_template("Implement [previous stage output]") == "Implement {previous_output}"
    assert normalize_prompt_template("Review [all stage outputs]") == "Review {all_stage_outputs}"
    assert normalize_prompt_template("Open [plan file]") == "Open {plan_path}"

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
        "input_template": "Review this: {previous_output}",
        "enabled": True,
    }


def test_add_session_stage_can_link_model_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()

    add_session_stage(config, "default", "review", "Review [previous stage output]", model_config="beta")

    stage = config.workflows["default"]["stages"][-1]
    assert stage["tool"] == "beta"


def test_unique_stage_id_suffixes_duplicate_instances():
    assert unique_stage_id({"planner", "coder", "coder-2"}, "coder") == "coder-3"
    assert unique_stage_id({"planner", "coder"}, "qa") == "qa"


def test_stage_library_options_use_configured_templates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    config.data["stage_library"] = [
        {"id": "qa", "tool": "beta", "input_template": "Review [previous stage output]"},
        {"id": "summary", "tool": "alpha", "input_template": "Summarize [user prompt]"},
    ]

    options = stage_library_options(config, "default")

    assert [(option.stage_id, option.tool) for option in options] == [("qa", "beta"), ("summary", "alpha")]
    assert options[0].prompt == "Review [previous stage output]"


def test_insert_stage_clones_library_template_with_unique_id_at_position(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    template = {"id": "alpha", "tool": "beta", "input_template": "Run [previous stage output]"}

    stage_id = insert_stage(config, "default", 1, template)

    stages = config.workflows["default"]["stages"]
    assert stage_id == "alpha-2"
    assert [stage["id"] for stage in stages] == ["alpha", "alpha-2", "beta"]
    assert stages[1] == {
        "id": "alpha-2",
        "tool": "beta",
        "input_template": "Run {previous_output}",
        "enabled": True,
    }


def test_move_workflow_stage_reorders_config_stages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()

    target = move_workflow_stage(config, "default", 1, -1)

    assert target == 0
    assert [stage["id"] for stage in config.workflows["default"]["stages"]] == ["beta", "alpha"]


def test_move_workflow_stage_ignores_out_of_range_move(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()

    target = move_workflow_stage(config, "default", 0, -1)

    assert target == 0
    assert [stage["id"] for stage in config.workflows["default"]["stages"]] == ["alpha", "beta"]


def test_tui_reorder_updates_workflow_stage_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    # Main menu -> Workflow; move cursor to the second stage, reorder it up, then quit.
    keys = iter(["\x1b[B", "\r", "\x1b[B", "u", "q"])

    run_tui(config, workflow_name="default", prompt="ignored", console=console, read_key=lambda: next(keys))

    assert [stage["id"] for stage in config.workflows["default"]["stages"]] == ["beta", "alpha"]


def test_tui_reorder_persists_to_project_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    write_stage_config(tmp_path)
    project_config = tmp_path / "cli-router.yaml"
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    # Main menu -> Workflow; move cursor to the second stage, reorder it up, then quit.
    keys = iter(["\x1b[B", "\r", "\x1b[B", "u", "q"])

    run_tui(load_config(), workflow_name="default", prompt="ignored", console=console, read_key=lambda: next(keys))

    saved = yaml.safe_load(project_config.read_text(encoding="utf-8"))
    assert [stage["id"] for stage in saved["workflows"]["default"]["stages"]] == ["beta", "alpha"]
    # The edit is written back to the project config, not the user config.
    assert not (tmp_path / "home" / ".cli-router" / "config.yaml").exists()


def test_remove_stage_removes_selected_workflow_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()

    removed = remove_stage(config, "default", 0)

    assert removed == "alpha"
    assert [stage["id"] for stage in config.workflows["default"]["stages"]] == ["beta"]


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
    keys = iter(["\x1b[B", "\x1b[B", "\r", "\x1b[B", "\r", *"Updated [user prompt]", "\x04", "\x1b[A", "\r", "q"])

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
    assert "Press B/Esc to go back" in output
    assert "Press Q to quit" in output
    assert "Model Config" in output
    assert "Tool" not in output
    assert "[user prompt]" in output
    assert "[previous stage output]" in output
    assert "a add" not in output


def test_stage_configuration_windows_rows_to_keep_chrome_visible_on_small_console(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    config.workflows["default"]["stages"] = [
        {
            "id": f"stage-{index:02}",
            "tool": "alpha",
            "input_template": f"Handle stage {index}: {{user_prompt}}",
        }
        for index in range(20)
    ]
    output_path = Path(tmp_path, "tui.txt")
    console = Console(
        file=open(output_path, "w", encoding="utf-8"),
        force_terminal=False,
        width=80,
        height=12,
    )

    tui._render_stage_configuration(console, config, "default", cursor=18)

    output = output_path.read_text(encoding="utf-8")
    assert "Stage configuration" in output
    assert "Press A to add a stage" in output
    assert "[user prompt]" in output
    assert "stage-18" in output
    assert "stage-00" not in output


def test_prompt_input_keeps_variable_legend_visible_while_composing(tmp_path):
    output_path = Path(tmp_path, "prompt.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter([*"Custom prompt", "\x1b"])

    result = tui._run_prompt_input(console, lambda: next(keys))

    assert result is None
    output = output_path.read_text(encoding="utf-8")
    assert "Stage prompt" in output
    assert "Ctrl+D to save" in output
    assert "[user prompt]" in output
    assert "[previous stage output]" in output
    assert "Custom prompt" in output


def test_prompt_input_renders_cursor_and_prominent_save_hint(tmp_path):
    output_path = Path(tmp_path, "prompt.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b"])

    result = tui._run_prompt_input(console, lambda: next(keys))

    assert result is None
    output = output_path.read_text(encoding="utf-8")
    assert "Stage prompt - Ctrl+D to save" in output
    assert "Enter newline. Empty line saves." in output
    assert "▏" in output


def test_prompt_input_accepts_multiline_text_with_ctrl_d_submit(tmp_path):
    output_path = Path(tmp_path, "prompt.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter([*"line one", "\r", *"line two", "\x04"])

    result = tui._run_prompt_input(console, lambda: next(keys))

    assert result == "line one\nline two"


def test_prompt_input_accepts_multiline_text_with_empty_line_submit(tmp_path):
    output_path = Path(tmp_path, "prompt.txt")
    console = Console(file=open(output_path, "w", encoding="utf-8"), force_terminal=False)
    keys = iter([*"line one", "\r", *"line two", "\r", "\r"])

    result = tui._run_prompt_input(console, lambda: next(keys))

    assert result == "line one\nline two"


def test_stage_configuration_add_stage_accepts_multiline_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(
        [
            "\x1b[B",
            "\x1b[B",
            "\r",
            "a",
            *"review",
            "\r",
            *"line one",
            "\r",
            *"line two [user prompt]",
            "\x04",
            "\r",
            "q",
        ]
    )

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    stage = config.workflows["default"]["stages"][-1]
    assert stage["id"] == "review"
    assert stage["input_template"] == "line one\nline two {user_prompt}"


def test_stage_configuration_inserts_stage_from_library(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    config.data["stage_library"] = [
        {"id": "beta", "tool": "alpha", "input_template": "Repeat [user prompt]"}
    ]
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\x1b[B", "\r", "i", "\r", "q"])

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    assert [stage["id"] for stage in config.workflows["default"]["stages"]] == ["alpha", "beta-2", "beta"]


def test_stage_configuration_removes_selected_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\x1b[B", "\r", "x", "q"])

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    assert [stage["id"] for stage in config.workflows["default"]["stages"]] == ["beta"]


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


def test_add_model_config_creates_provider_tool_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()

    add_model_config(config, "grok-coder", provider="grok", model="grok-build", effort="high")

    assert config.tools["grok-coder"] == {
        "type": "grok",
        "provider": "grok",
        "model": "grok-build",
        "effort": "high",
        "command": ["grok", "--single", "{prompt}"],
        "output": {"format": "text"},
    }


def test_add_model_config_rejects_duplicate_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_stage_config(tmp_path)
    config = load_config()

    try:
        add_model_config(config, "alpha", provider="grok", model="grok-build", effort="high")
    except KeyError as exc:
        assert "Model config already exists: alpha" in str(exc)
    else:
        raise AssertionError("duplicate model config name should fail")


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
    assert "Press A to add a model config" in output


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


def test_model_config_screen_adds_provider_and_persists_to_home_config(tmp_path, monkeypatch):
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
    monkeypatch.setattr("cli_router.tui.model_options_for_provider", lambda provider: [f"{provider}-model"])
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(
        [
            "\x1b[B",
            "\x1b[B",
            "\x1b[B",
            "\r",
            "a",
            "\x1b[B",
            "\x1b[B",
            "\x1b[B",
            "\r",
            "\r",
            "\x1b[B",
            "\r",
            *"grok-coder",
            "\r",
            "q",
        ]
    )

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["tools"]["grok-coder"]["provider"] == "grok"
    assert saved["tools"]["grok-coder"]["model"] == "grok-model"
    assert saved["tools"]["grok-coder"]["effort"] == "high"
    assert saved["tools"]["grok-coder"]["command"] == ["grok", "--single", "{prompt}"]


def test_model_config_screen_adds_when_no_model_configs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cli_router.tui.model_options_for_provider", lambda provider: [f"{provider}-model"])
    config = RouterConfig(
        {"version": 1, "tools": {}, "workflows": {"default": {"stages": []}}},
        tmp_path / "cli-router.yaml",
    )
    console = Console(file=open(Path(tmp_path, "tui.txt"), "w", encoding="utf-8"), force_terminal=False)
    keys = iter(["\x1b[B", "\x1b[B", "\x1b[B", "\r", "a", "\r", "\r", "\r", *"codex", "\r", "q"])

    exit_code = run_tui(config, workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    assert config.tools["codex"]["provider"] == "codex"


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
    assert "grok" in output


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
    keys = iter(["\x1b[B", "\x1b[B", "\r", "a", *"review", "\r", *"Review [previous stage output]", "\x04", "\r", "q"])

    exit_code = run_tui(load_config(), workflow_name="default", prompt=None, console=console, read_key=lambda: next(keys))

    assert exit_code == 0
    saved = config_path.read_text(encoding="utf-8")
    assert "id: review" in saved
    assert "input_template: Review {previous_output}" in saved
