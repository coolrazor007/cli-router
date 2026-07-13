"""Interactive terminal UI for selecting workflow stages."""

from __future__ import annotations

import os
import re
import signal
import sys
from collections import deque
from copy import deepcopy
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from rich import box as rich_box
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .config import RouterConfig, save_config, user_config_path
from .models import PROVIDERS, _provider_command, model_options_for_provider, provider_tool_config
from .modelcache import ModelCache
from .streamfmt import OutputCondenser, condense_extracted, first_meaningful_line, strip_ansi
from .workflows import StageSummary, WorkflowSummary, run_workflow


KeyReader = Callable[[], str]
T = TypeVar("T")


OFFICIAL_PROMPT_VARIABLES = {
    "[user prompt]": ("{user_prompt}", "Original request entered in the Prompt menu"),
    "[previous stage output]": (
        "{previous_output}",
        "Final extracted output of the immediately preceding stage",
    ),
    "[all stage outputs]": (
        "{all_stage_outputs}",
        "Final outputs of every completed stage so far, labeled by stage",
    ),
    "[plan file]": ("{plan_path}", "Path to the workflow plan file (PLAN.md)"),
}
PROMPT_CURSOR = "▏"
PROMPT_VARIABLE_PATTERN = re.compile(r"\[[^\]]+\]")
RESIZE_REDRAW_KEY = "\x00resize-redraw"
MIN_TUI_WIDTH = 80
MIN_TUI_HEIGHT = 24
TUI_THEME = {
    "accent": "cyan",
    "selection": "reverse bold cyan",
    "error": "bold red",
    "success": "bold green",
    "warning": "bold yellow",
    "muted": "dim",
    "title": "bold",
}


class PromptVariableError(ValueError):
    """Raised when a TUI prompt contains an unknown bracket variable."""


@dataclass(frozen=True)
class StageOption:
    stage_id: str
    tool: str
    selected: bool
    prompt: str


@dataclass(frozen=True)
class ModelConfigOption:
    name: str
    provider: str
    model: str
    effort: str


@dataclass(frozen=True)
class KeyBinding:
    context: str
    keys: str
    action: str
    footer: bool = False


@dataclass
class _RawTerminalState:
    fd: int
    previous: Any
    active: bool = False


_ACTIVE_RAW_TERMINAL: _RawTerminalState | None = None
_RESIZE_REDRAW_PENDING = False


KEY_BINDINGS: tuple[KeyBinding, ...] = (
    KeyBinding("main", "↑/↓ or k/j", "Move", True),
    KeyBinding("main", "1-5", "Jump", True),
    KeyBinding("main", "Enter", "Open", True),
    KeyBinding("main", "Esc/q", "Quit", True),
    KeyBinding("main", "?", "Help", True),
    KeyBinding("first_run", "↑/↓ or k/j", "Move", True),
    KeyBinding("first_run", "Space", "Toggle provider", True),
    KeyBinding("first_run", "Enter", "Save config", True),
    KeyBinding("first_run", "Esc/q", "Cancel", True),
    KeyBinding("first_run", "?", "Help", True),
    KeyBinding("workflow", "↑/↓ or k/j", "Move", True),
    KeyBinding("workflow", "g/G", "Top/bottom", False),
    KeyBinding("workflow", "Space", "Toggle", True),
    KeyBinding("workflow", "U/D", "Reorder", False),
    KeyBinding("workflow", "I/A", "Insert stage", False),
    KeyBinding("workflow", "X", "Remove", False),
    KeyBinding("workflow", "Enter", "Run checked stages", True),
    KeyBinding("workflow", "B/Esc", "Go back", True),
    KeyBinding("workflow", "Q", "Quit", True),
    KeyBinding("workflow", "?", "Help", True),
    KeyBinding("stage_config", "↑/↓ or k/j", "Select stage", True),
    KeyBinding("stage_config", "g/G", "Top/bottom", False),
    KeyBinding("stage_config", "Enter/E", "Edit selected stage", True),
    KeyBinding("stage_config", "A", "Add a stage", True),
    KeyBinding("stage_config", "I", "Insert from library", False),
    KeyBinding("stage_config", "X", "Remove selected stage", False),
    KeyBinding("stage_config", "M", "Change model config", True),
    KeyBinding("stage_config", "B/Esc", "Go back", True),
    KeyBinding("stage_config", "Q", "Quit", True),
    KeyBinding("stage_config", "?", "Help", True),
    KeyBinding("model_config", "↑/↓ or k/j", "Select model config", True),
    KeyBinding("model_config", "g/G", "Top/bottom", False),
    KeyBinding("model_config", "A", "Add a model config", True),
    KeyBinding("model_config", "E", "Edit model config", True),
    KeyBinding("model_config", "B/Esc", "Go back", True),
    KeyBinding("model_config", "Q", "Quit", True),
    KeyBinding("model_config", "?", "Help", True),
    KeyBinding("picker", "↑/↓ or k/j", "Move", True),
    KeyBinding("picker", "g/G", "Top/bottom", False),
    KeyBinding("picker", "Enter", "Select", True),
    KeyBinding("picker", "B/Esc", "Go back", True),
    KeyBinding("picker", "Q", "Cancel", True),
    KeyBinding("picker", "?", "Help", True),
    KeyBinding("text_input", "Enter", "Save", True),
    KeyBinding("text_input", "Esc", "Cancel", True),
    KeyBinding("text_input", "Backspace", "Delete", True),
    KeyBinding("text_input", "?", "Type literal ?", False),
    KeyBinding("prompt_input", "Ctrl+D", "Save", True),
    KeyBinding("prompt_input", "Enter", "Newline; empty line saves", True),
    KeyBinding("prompt_input", "Esc", "Cancel", True),
    KeyBinding("prompt_input", "Backspace", "Delete", True),
    KeyBinding("help", "B/Esc/Enter/?", "Go back", True),
    KeyBinding("help", "Q", "Quit", True),
)


def run_tui(
    config: RouterConfig,
    workflow_name: str = "default",
    prompt: str | None = None,
    *,
    console: Console | None = None,
    read_key: KeyReader | None = None,
) -> int:
    console = console or Console()
    read_key = read_key or _read_key
    prompt = prompt or ""
    persistent = _should_persist(config)
    screen_context = console.screen() if console.is_terminal else nullcontext()
    screen_active = False

    def enter_screen() -> None:
        nonlocal screen_active
        if not screen_active:
            screen_context.__enter__()
            screen_active = True

    def leave_screen() -> None:
        nonlocal screen_active
        if screen_active:
            screen_context.__exit__(None, None, None)
            screen_active = False

    signal_handlers = _TuiSignalHandlers(
        enabled=console.is_terminal and os.name != "nt",
        enter_screen=enter_screen,
        leave_screen=leave_screen,
        show_cursor=console.show_cursor,
    )

    console.show_cursor(False)
    try:
        signal_handlers.__enter__()
        try:
            enter_screen()
            if config.source is None and not user_config_path().exists():
                selected_providers = _run_first_run_provider_screen(console, read_key)
                if selected_providers is None:
                    return 0
                _configure_first_run(config, selected_providers)
                _persist_if_needed(config, True)
                persistent = True

            menu_items = ["Prompt", "Workflow", "Stage configuration", "Model Config", "Quit"]
            cursor = 0

            while True:
                if _render_if_too_small(console):
                    key = _read_tui_key(read_key)
                    if key in {"q", "Q", "\x1b"}:
                        return 0
                    continue
                _render_main_menu(console, workflow_name, prompt, menu_items, cursor)
                key = _read_tui_key(read_key)
                if key in {"q", "Q", "\x1b"}:
                    return 0
                if key == "?":
                    result = _run_help_screen(console, read_key, current_context="main")
                    if result is not None:
                        return result
                    continue
                if key in {str(index) for index in range(1, len(menu_items) + 1)}:
                    cursor = int(key) - 1
                    continue
                if key in {"\x1b[A", "k", "K"}:
                    cursor = _move_cursor(cursor, -1, len(menu_items))
                    continue
                if key in {"\x1b[B", "j", "J"}:
                    cursor = _move_cursor(cursor, 1, len(menu_items))
                    continue
                if key not in {"\r", "\n"}:
                    continue

                choice = menu_items[cursor]
                if choice == "Workflow":
                    result = _run_workflow_screen(
                        config,
                        workflow_name,
                        prompt,
                        console,
                        read_key,
                        persistent,
                        before_run=leave_screen,
                    )
                    if result is not None:
                        return result
                    continue
                if choice == "Prompt":
                    prompt = _run_text_input(console, "Prompt", read_key)
                    if prompt is None:
                        continue
                    leave_screen()
                    console.show_cursor(True)
                    summary = _run_workflow_with_feedback(config, workflow_name, prompt, console)
                    return summary.exit_code
                if choice == "Stage configuration":
                    result = _run_stage_configuration_screen(config, workflow_name, console, read_key, persistent)
                    if result is not None:
                        return result
                    continue
                if choice == "Model Config":
                    result = _run_model_config_screen(config, console, read_key, persistent)
                    if result is not None:
                        return result
                    continue
                return 0
        except KeyboardInterrupt:
            leave_screen()
            console.show_cursor(True)
            console.print("Canceled")
            return 130
    finally:
        signal_handlers.__exit__(None, None, None)
        leave_screen()
        console.show_cursor(True)


class _TuiSignalHandlers:
    def __init__(
        self,
        *,
        enabled: bool,
        enter_screen: Callable[[], None],
        leave_screen: Callable[[], None],
        show_cursor: Callable[[bool], None],
    ) -> None:
        self.enabled = enabled
        self.enter_screen = enter_screen
        self.leave_screen = leave_screen
        self.show_cursor = show_cursor
        self._old_handlers: dict[int, signal.Handlers] = {}

    def __enter__(self) -> _TuiSignalHandlers:
        if not self.enabled:
            return self
        self._install("SIGWINCH", self._handle_resize)
        self._install("SIGTSTP", self._handle_suspend)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        for signum, old_handler in reversed(self._old_handlers.items()):
            signal.signal(signum, old_handler)
        self._old_handlers.clear()

    def _install(self, name: str, handler: Callable[[int, Any], None]) -> None:
        signum = getattr(signal, name, None)
        if signum is None:
            return
        try:
            self._old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, handler)
        except (OSError, RuntimeError, ValueError):
            self._old_handlers.pop(signum, None)

    def _handle_resize(self, signum: int | None, frame: Any) -> None:
        _request_resize_redraw()

    def _handle_suspend(self, signum: int | None, frame: Any) -> None:
        if not hasattr(signal, "SIGTSTP"):
            return
        _restore_active_raw_terminal()
        self.leave_screen()
        self.show_cursor(True)
        previous_handler = signal.getsignal(signal.SIGTSTP)
        try:
            signal.signal(signal.SIGTSTP, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTSTP)
        finally:
            signal.signal(signal.SIGTSTP, previous_handler)
        self.enter_screen()
        self.show_cursor(False)
        _resume_active_raw_terminal()
        _request_resize_redraw()


def _request_resize_redraw() -> None:
    global _RESIZE_REDRAW_PENDING
    _RESIZE_REDRAW_PENDING = True


def _consume_resize_redraw() -> bool:
    global _RESIZE_REDRAW_PENDING
    pending = _RESIZE_REDRAW_PENDING
    _RESIZE_REDRAW_PENDING = False
    return pending


def _run_workflow_screen(
    config: RouterConfig,
    workflow_name: str,
    prompt: str,
    console: Console,
    read_key: KeyReader,
    persistent: bool,
    before_run: Callable[[], None] | None = None,
) -> int | None:
    options = stage_options_for_workflow(config, workflow_name)
    if not options and not stage_library_options(config, workflow_name):
        console.print(f"[{TUI_THEME['error']}]No stages are configured for this workflow.[/]")
        return 2

    selected = [option.selected for option in options]
    cursor = 0

    while True:
        if _render_if_too_small(console):
            key = _read_tui_key(read_key)
            if key in {"q", "Q"}:
                return 0
            if key in {"b", "B", "\x1b"}:
                return None
            continue
        if options:
            cursor %= len(options)
        else:
            cursor = 0
        _render(console, workflow_name, prompt, options, selected, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return 0
        if key == "?":
            result = _run_help_screen(console, read_key, current_context="workflow")
            if result is not None:
                return result
            continue
        if options and key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(options))
            continue
        if options and key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(options))
            continue
        if options and key in {"g", "h", "H"}:
            cursor = 0
            continue
        if options and key in {"G", "l", "L"}:
            cursor = len(options) - 1
            continue
        if options and key == " ":
            selected = toggle_selection(selected, cursor)
            continue
        if options and key in {"u", "U"}:
            options, selected, cursor = _reorder_stage(config, workflow_name, options, selected, cursor, -1, persistent)
            continue
        if options and key in {"d", "D"}:
            options, selected, cursor = _reorder_stage(config, workflow_name, options, selected, cursor, 1, persistent)
            continue
        if key in {"a", "A", "i", "I"}:
            template = _run_stage_library_picker(config, workflow_name, console, read_key)
            if template is None:
                continue
            old_selected = _selected_by_stage_id(options, selected)
            insert_at = _insert_index_after_option(config, workflow_name, options[cursor] if options else None)
            stage_id = insert_stage(config, workflow_name, insert_at, template)
            _persist_if_needed(config, persistent)
            options = stage_options_for_workflow(config, workflow_name)
            selected = [old_selected.get(option.stage_id, option.selected) for option in options]
            if stage_id in [option.stage_id for option in options]:
                cursor = [option.stage_id for option in options].index(stage_id)
                selected[cursor] = True
            continue
        if options and key in {"x", "X"}:
            old_selected = _selected_by_stage_id(options, selected)
            removed_id = options[cursor].stage_id
            remove_stage(config, workflow_name, _workflow_stage_index(config, workflow_name, removed_id))
            old_selected.pop(removed_id, None)
            _persist_if_needed(config, persistent)
            options = stage_options_for_workflow(config, workflow_name)
            selected = [old_selected.get(option.stage_id, option.selected) for option in options]
            if options:
                cursor = min(cursor, len(options) - 1)
            continue
        if key in {"\r", "\n"}:
            stage_names = selected_stage_names(options, selected)
            if not stage_names:
                console.print(f"[{TUI_THEME['error']}]Select at least one stage.[/]")
                continue
            if before_run is not None:
                before_run()
                console.show_cursor(True)
            summary = _run_workflow_with_feedback(config, workflow_name, prompt, console, stage_names=stage_names)
            return summary.exit_code


def stage_options_for_workflow(config: RouterConfig, workflow_name: str = "default") -> list[StageOption]:
    try:
        workflow = config.workflows[workflow_name]
    except KeyError as exc:
        raise KeyError(f"Unknown workflow: {workflow_name}") from exc

    options: list[StageOption] = []
    for stage in workflow.get("stages", []):
        options.append(
            StageOption(
                stage_id=str(stage["id"]),
                tool=str(stage["tool"]),
                selected=stage.get("enabled", True) is not False,
                prompt=prompt_preview(str(stage.get("input_template", "{user_prompt}")), ""),
            )
        )
    return options


def model_configs_for_config(config: RouterConfig) -> list[ModelConfigOption]:
    options: list[ModelConfigOption] = []
    for name, tool in config.tools.items():
        options.append(
            ModelConfigOption(
                name=str(name),
                provider=str(tool.get("provider") or tool.get("type") or "generic"),
                model=str(tool.get("model") or "<unset>"),
                effort=str(tool.get("effort") or "<unset>"),
            )
        )
    return options


def update_model_config(
    config: RouterConfig,
    name: str,
    *,
    provider: str,
    model: str,
    effort: str,
) -> None:
    tool = config.tools[name]
    tool["provider"] = provider
    tool["model"] = model
    tool["effort"] = effort
    # Regenerate the command so the edited model is actually routed to the CLI.
    # Only known providers own their command shape; leave custom/generic tools
    # (which carry hand-written commands) untouched.
    if provider in PROVIDERS:
        tool["type"] = provider
        tool["command"] = _provider_command(provider, model, effort)


def add_model_config(
    config: RouterConfig,
    name: str,
    *,
    provider: str,
    model: str,
    effort: str,
) -> None:
    if name in config.tools:
        raise KeyError(f"Model config already exists: {name}")
    config.tools[name] = provider_tool_config(provider, model, effort)


def set_stage_model_config(config: RouterConfig, workflow_name: str, stage_id: str, model_config: str) -> None:
    for stage in config.workflows[workflow_name].get("stages", []):
        if stage["id"] == stage_id:
            stage["tool"] = model_config
            return
    raise KeyError(f"Unknown stage: {stage_id}")


def set_stage_prompt(config: RouterConfig, workflow_name: str, stage_id: str, prompt: str) -> None:
    for stage in config.workflows[workflow_name].get("stages", []):
        if stage["id"] == stage_id:
            stage["input_template"] = normalize_prompt_template(prompt)
            return
    raise KeyError(f"Unknown stage: {stage_id}")


def unique_stage_id(existing_ids: set[str], base_id: str) -> str:
    if base_id not in existing_ids:
        return base_id
    suffix = 2
    while f"{base_id}-{suffix}" in existing_ids:
        suffix += 1
    return f"{base_id}-{suffix}"


def stage_library_options(config: RouterConfig, workflow_name: str = "default") -> list[StageOption]:
    templates = config.data.get("stage_library")
    if templates is None:
        templates = config.workflows.get(workflow_name, {}).get("stages", [])
    options: list[StageOption] = []
    for template in templates:
        options.append(
            StageOption(
                stage_id=str(template["id"]),
                tool=str(template["tool"]),
                selected=True,
                prompt=prompt_preview(str(template.get("input_template", "{user_prompt}")), ""),
            )
        )
    return options


def insert_stage(
    config: RouterConfig,
    workflow_name: str,
    index: int,
    template: dict[str, Any],
) -> str:
    workflow = config.workflows[workflow_name]
    stages = workflow.setdefault("stages", [])
    tool_name = str(template["tool"])
    if tool_name not in config.tools:
        raise KeyError(f"Unknown model config: {tool_name}")

    existing_ids = {str(stage["id"]) for stage in stages}
    stage_id = unique_stage_id(existing_ids, str(template["id"]))
    stage = deepcopy(template)
    stage["id"] = stage_id
    stage["tool"] = tool_name
    stage["input_template"] = normalize_prompt_template(str(stage.get("input_template", "{user_prompt}")))
    stage["enabled"] = stage.get("enabled", True) is not False
    stage.pop("label", None)
    insert_at = max(0, min(index, len(stages)))
    stages.insert(insert_at, stage)
    return stage_id


def remove_stage(config: RouterConfig, workflow_name: str, index: int) -> str:
    stages = config.workflows[workflow_name].setdefault("stages", [])
    if index < 0 or index >= len(stages):
        raise IndexError("stage index out of range")
    removed = stages.pop(index)
    return str(removed["id"])


def move_workflow_stage(config: RouterConfig, workflow_name: str, index: int, delta: int) -> int:
    stages = config.workflows[workflow_name].setdefault("stages", [])
    target = index + delta
    if index < 0 or index >= len(stages) or target < 0 or target >= len(stages):
        return index
    stages[index], stages[target] = stages[target], stages[index]
    return target


def add_session_stage(
    config: RouterConfig,
    workflow_name: str,
    stage_id: str,
    prompt: str,
    model_config: str | None = None,
) -> None:
    workflow = config.workflows[workflow_name]
    stages = workflow.setdefault("stages", [])
    tool_name = model_config or (stages[0]["tool"] if stages else next(iter(config.tools)))
    if tool_name not in config.tools:
        raise KeyError(f"Unknown model config: {tool_name}")
    insert_stage(
        config,
        workflow_name,
        len(stages),
        {
            "id": stage_id,
            "tool": tool_name,
            "input_template": prompt,
        },
    )


def _should_persist(config: RouterConfig) -> bool:
    # Persist TUI edits back to whatever config is in use: the project-local
    # cli-router.yaml, an explicit --config path, or the user config. A missing
    # source means we will create the user config on first write.
    return True


def _persist_if_needed(config: RouterConfig, persistent: bool) -> None:
    if persistent:
        save_config(config, config.source)


def _configure_first_run(config: RouterConfig, providers: list[str]) -> None:
    first_provider = providers[0]
    cache = ModelCache.load()
    tools = {
        provider: provider_tool_config(provider, model_options_for_provider(provider, cache=cache)[0])
        for provider in providers
    }
    config.data.clear()
    config.data.update(
        {
            "version": 1,
            "defaults": {
                "plan_file": "PLAN.md",
                "run_dir": ".cli-router/runs",
                "stop_on_failure": True,
                "tui_verbosity": "condensed",
            },
            "tools": tools,
            "stage_library": [
                {
                    "id": "planner",
                    "tool": first_provider,
                    "input_template": normalize_prompt_template(
                        "Plan this change: [user prompt]\n\n"
                        "Do not edit any files. Respond only with a detailed implementation plan in Markdown."
                    ),
                    "output_file": "PLAN.md",
                },
                {
                    "id": "coder",
                    "tool": first_provider,
                    "input_template": normalize_prompt_template(
                        "Please implement the plan in [plan file].\n\nOriginal request: [user prompt]"
                    ),
                },
                {
                    "id": "qa",
                    "tool": first_provider,
                    "input_template": normalize_prompt_template(
                        "Review the previous stage's work and report problems.\n\n"
                        "Previous stage output:\n[previous stage output]\n\n"
                        "Plan: [plan file]\nOriginal request: [user prompt]\n\n"
                        "Do not edit any files. Respond only in Markdown."
                    ),
                },
                {
                    "id": "summary",
                    "tool": first_provider,
                    "input_template": normalize_prompt_template(
                        "Summarize the completed workflow for the user.\n\n"
                        "Original request: [user prompt]\n\nAll stage outputs:\n[all stage outputs]"
                    ),
                },
            ],
            "workflows": {
                "default": {
                    "stages": [
                        {
                            "id": "planner",
                            "tool": first_provider,
                            "input_template": normalize_prompt_template(
                                "Plan this change: [user prompt]\n\n"
                                "Do not edit any files. Respond only with a detailed implementation plan in Markdown."
                            ),
                            "output_file": "PLAN.md",
                        },
                        {
                            "id": "coder",
                            "tool": first_provider,
                            "input_template": normalize_prompt_template(
                                "Please implement the plan in [plan file].\n\nOriginal request: [user prompt]"
                            ),
                        },
                    ]
                }
            },
        }
    )


def toggle_selection(selected: list[bool], index: int) -> list[bool]:
    updated = list(selected)
    updated[index] = not updated[index]
    return updated


def move_stage(
    options: list[StageOption],
    selected: list[bool],
    index: int,
    delta: int,
) -> tuple[list[StageOption], list[bool], int]:
    target = index + delta
    if target < 0 or target >= len(options):
        return options, selected, index

    updated_options = list(options)
    updated_selected = list(selected)
    updated_options[index], updated_options[target] = updated_options[target], updated_options[index]
    updated_selected[index], updated_selected[target] = updated_selected[target], updated_selected[index]
    return updated_options, updated_selected, target


def _reorder_stage(
    config: RouterConfig,
    workflow_name: str,
    options: list[StageOption],
    selected: list[bool],
    index: int,
    delta: int,
    persistent: bool,
) -> tuple[list[StageOption], list[bool], int]:
    old_selected = _selected_by_stage_id(options, selected)
    cursor = move_workflow_stage(config, workflow_name, index, delta)
    _persist_if_needed(config, persistent)
    options = stage_options_for_workflow(config, workflow_name)
    selected = [old_selected.get(option.stage_id, option.selected) for option in options]
    return options, selected, cursor


def selected_stage_names(options: list[StageOption], selected: list[bool]) -> list[str]:
    return [option.stage_id for option, is_selected in zip(options, selected) if is_selected]


def _selected_by_stage_id(options: list[StageOption], selected: list[bool]) -> dict[str, bool]:
    return {option.stage_id: is_selected for option, is_selected in zip(options, selected)}


def _workflow_stage_index(config: RouterConfig, workflow_name: str, stage_id: str) -> int:
    for index, stage in enumerate(config.workflows[workflow_name].get("stages", [])):
        if str(stage["id"]) == stage_id:
            return index
    raise KeyError(f"Unknown stage: {stage_id}")


def _insert_index_after_option(
    config: RouterConfig,
    workflow_name: str,
    option: StageOption | None,
) -> int:
    if option is None:
        return len(config.workflows[workflow_name].get("stages", []))
    return _workflow_stage_index(config, workflow_name, option.stage_id) + 1


def prompt_preview(template: str, user_prompt: str) -> str:
    preview = template
    for display_token, (placeholder, _description) in OFFICIAL_PROMPT_VARIABLES.items():
        preview = preview.replace(placeholder, display_token)
    preview = preview.replace("{user_prompt}", user_prompt or "[user prompt]")
    preview = preview.replace("{prompt}", "[stage prompt]")
    return " ".join(preview.split())


def _prompt_template_for_edit(template: str) -> str:
    value = template
    for display_token, (placeholder, _description) in OFFICIAL_PROMPT_VARIABLES.items():
        value = value.replace(placeholder, display_token)
    return value.replace("{user_prompt}", "[user prompt]").replace("{prompt}", "[stage prompt]")


def normalize_prompt_template(template: str) -> str:
    normalized = template
    for token in PROMPT_VARIABLE_PATTERN.findall(template):
        if token not in OFFICIAL_PROMPT_VARIABLES:
            raise PromptVariableError(f"Unknown prompt variable: {token}")
        normalized = normalized.replace(token, OFFICIAL_PROMPT_VARIABLES[token][0])
    return normalized


def _move_cursor(cursor: int, delta: int, count: int) -> int:
    return (cursor + delta) % count


def _cursor_top(count: int) -> int:
    return 0 if count else 0


def _cursor_bottom(count: int) -> int:
    return max(0, count - 1)


def _ui_table(*, show_header: bool = True, **kwargs: Any) -> Table:
    # Keep the surrounding Panel as the only frame: headered tables get a plain
    # header rule (SIMPLE_HEAD), headerless tables get no box at all. This avoids
    # nesting a full table border inside every Panel.
    kwargs.setdefault("expand", True)
    if show_header:
        kwargs.setdefault("box", rich_box.SIMPLE_HEAD)
        kwargs.setdefault("header_style", TUI_THEME["title"])
    else:
        kwargs.setdefault("box", None)
    return Table(show_header=show_header, **kwargs)


def _read_tui_key(read_key: KeyReader) -> str:
    key = read_key()
    if key == "\x03":
        raise KeyboardInterrupt
    return key


def _supports_unicode() -> bool:
    if os.environ.get("CLI_ROUTER_TUI_ASCII") in {"1", "true", "TRUE", "yes", "YES"}:
        return False
    locale_name = " ".join(
        value for value in (os.environ.get("LC_ALL"), os.environ.get("LC_CTYPE"), os.environ.get("LANG")) if value
    )
    return "UTF-8" in locale_name.upper() or "UTF8" in locale_name.upper()


def _checkbox(selected: bool) -> str:
    if _supports_unicode():
        return "☑" if selected else "☐"
    return "[x]" if selected else "[ ]"


def _status_text(returncode: int) -> str:
    if _supports_unicode():
        marker = "✓" if returncode == 0 else "✗"
    else:
        marker = "OK" if returncode == 0 else "ERR"
    return f"{marker} exit {returncode}"


def _footer_for(context: str) -> str:
    bindings = [binding for binding in KEY_BINDINGS if binding.context == context and binding.footer]
    return " · ".join(f"{_compact_keys(binding.keys)} {_compact_action(binding.action)}" for binding in bindings)


def _compact_keys(keys: str) -> str:
    return keys.replace("↑/↓ or k/j", "↑/↓ k/j")


def _compact_action(action: str) -> str:
    compact_actions = {
        "Go back": "back",
        "Run checked stages": "run",
        "Select stage": "select",
        "Select model config": "select",
        "Toggle provider": "toggle",
        "Save config": "save",
        "Add a stage": "add",
        "Add a model config": "add",
        "Edit selected stage": "edit",
        "Edit model config": "edit",
    }
    return compact_actions.get(action, action.lower())


def _action_footer_for(context: str, *actions: str) -> str:
    requested = set(actions)
    bindings = [
        binding
        for binding in KEY_BINDINGS
        if binding.context == context and binding.action in requested
    ]
    sentences = [f"Press {binding.keys} to {binding.action.lower()}." for binding in bindings]
    midpoint = (len(sentences) + 1) // 2
    return "\n".join((" ".join(sentences[:midpoint]), " ".join(sentences[midpoint:]))).strip()


def _help_table(current_context: str | None = None) -> Table:
    table = _ui_table()
    table.add_column("Screen")
    table.add_column("Keys")
    table.add_column("Action")
    for binding in KEY_BINDINGS:
        screen = binding.context.replace("_", " ").title()
        style = TUI_THEME["selection"] if current_context == binding.context else ""
        table.add_row(screen, binding.keys, binding.action, style=style)
    return table


def _run_help_screen(console: Console, read_key: KeyReader, *, current_context: str | None = None) -> int | None:
    while True:
        _render_help_screen(console, current_context=current_context)
        key = _read_tui_key(read_key)
        if key in {"q", "Q"}:
            return 0
        if key in {"b", "B", "\x1b", "\r", "\n", "?"}:
            return None


def _render_help_screen(console: Console, *, current_context: str | None = None) -> None:
    console.clear()
    console.print(Panel(_help_table(current_context), title="Keyboard help", subtitle=_footer_for("help")))


def _terminal_too_small(console: Console, min_width: int = MIN_TUI_WIDTH, min_height: int = MIN_TUI_HEIGHT) -> bool:
    return console.size.width < min_width or console.size.height < min_height


def _render_if_too_small(console: Console) -> bool:
    if not console.is_terminal or not _terminal_too_small(console):
        return False
    _render_too_small(console)
    return True


def _render_too_small(console: Console) -> None:
    console.clear()
    width, height = console.size.width, console.size.height
    message = (
        f"Terminal too small: {width}x{height}. "
        f"Need at least {MIN_TUI_WIDTH}x{MIN_TUI_HEIGHT}. Resize, then press any key."
    )
    console.print(Panel(message, title="CLI-Router", subtitle="Press Q to quit · Press ? for help"))


def _run_text_input(
    console: Console,
    title: str,
    read_key: KeyReader,
    initial: str = "",
) -> str | None:
    value = initial
    initial_selected = bool(initial)
    while True:
        _render_text_input(console, title, value)
        key = _read_tui_key(read_key)
        if key == "\x1b":
            return None
        if key in {"\r", "\n"}:
            return value
        if key in {"\x7f", "\b"}:
            initial_selected = False
            value = value[:-1]
            continue
        if len(key) == 1 and key.isprintable():
            if initial_selected:
                value = ""
                initial_selected = False
            value += key


def _render_text_input(console: Console, title: str, value: str) -> None:
    console.clear()
    console.print(
        Panel(
            escape(value or "<empty>"),
            title=title,
            subtitle=_footer_for("text_input"),
        )
    )


def _run_prompt_input(
    console: Console,
    read_key: KeyReader,
    *,
    initial: str = "",
) -> str | None:
    value = initial
    initial_selected = bool(initial)
    while True:
        _render_prompt_input(console, "Stage prompt", value)
        key = _read_tui_key(read_key)
        if key == "\x1b":
            return None
        if key == "\x04":
            return value
        if key in {"\r", "\n"}:
            if initial_selected:
                value = ""
                initial_selected = False
            elif value.endswith("\n"):
                return value.rstrip("\n")
            value += "\n"
            continue
        if key in {"\x7f", "\b"}:
            initial_selected = False
            value = value[:-1]
            continue
        if len(key) == 1 and key.isprintable():
            if initial_selected:
                value = ""
                initial_selected = False
            value += key


def _render_prompt_input(console: Console, title: str, value: str) -> None:
    console.clear()
    display_value = f"{escape(value)}{PROMPT_CURSOR}" if value else PROMPT_CURSOR
    console.print(
        Group(
            _prompt_variable_table(),
            Panel(
                display_value,
                title=f"{title} - Ctrl+D to save",
                subtitle=_footer_for("prompt_input"),
            ),
            "Enter newline. Empty line saves. Esc cancels. Backspace deletes.",
        )
    )


def _run_workflow_with_feedback(
    config: RouterConfig,
    workflow_name: str,
    prompt: str,
    console: Console,
    stage_names: list[str] | None = None,
) -> WorkflowSummary:
    console.clear()
    _print_running_workflow_panel(console, workflow_name, prompt, stage_names)
    if _tui_verbosity(config) == "full":
        summary = run_workflow(config, prompt, workflow_name, stage_names=stage_names)
        _print_summary(console, summary)
        return summary

    observer = _TuiObserver(console)
    with observer.live_context():
        summary = run_workflow(config, prompt, workflow_name, stage_names=stage_names, observer=observer)
    _print_compact_summary(console, summary)
    return summary


def _print_running_workflow_panel(
    console: Console,
    workflow_name: str,
    prompt: str,
    stage_names: list[str] | None,
) -> None:
    table = _ui_table(show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Workflow", workflow_name)
    table.add_row("Prompt", prompt or "<empty>")
    if stage_names is not None:
        table.add_row("Stages", ", ".join(stage_names))
    console.print(Panel(table, title="Running workflow", subtitle="Streaming condensed output."))


def _tui_verbosity(config: RouterConfig) -> str:
    value = str(config.defaults.get("tui_verbosity", "condensed")).lower()
    return "full" if value == "full" else "condensed"


def _render_main_menu(
    console: Console,
    workflow_name: str,
    prompt: str,
    menu_items: list[str],
    cursor: int,
) -> None:
    console.clear()
    table = _ui_table(show_header=False)
    table.add_column("Menu")
    for index, item in enumerate(menu_items):
        style = TUI_THEME["selection"] if index == cursor else ""
        table.add_row(item, style=style)
    console.print(
        Panel(
            table,
            title="CLI-Router",
            subtitle=f"Workflow: {workflow_name}  {_footer_for('main')}",
        )
    )


def _run_first_run_provider_screen(console: Console, read_key: KeyReader) -> list[str] | None:
    selected = [False for _provider in PROVIDERS]
    cursor = 0
    while True:
        if _render_if_too_small(console):
            key = _read_tui_key(read_key)
            if key in {"q", "Q", "\x1b"}:
                return None
            continue
        _render_first_run_provider_screen(console, selected, cursor)
        key = _read_tui_key(read_key)
        if key in {"q", "Q", "\x1b"}:
            return None
        if key == "?":
            result = _run_help_screen(console, read_key, current_context="first_run")
            if result is not None:
                return None
            continue
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(PROVIDERS))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(PROVIDERS))
            continue
        if key == " ":
            selected[cursor] = not selected[cursor]
            continue
        if key in {"\r", "\n"}:
            providers = [provider for provider, is_selected in zip(PROVIDERS, selected) if is_selected]
            if providers:
                return providers
            console.print(f"[{TUI_THEME['error']}]Select at least one provider.[/]")


def _render_first_run_provider_screen(console: Console, selected: list[bool], cursor: int) -> None:
    console.clear()
    table = _ui_table()
    table.add_column("Use", width=5, justify="center", no_wrap=True)
    table.add_column("Provider")
    for index, provider in enumerate(PROVIDERS):
        checkbox = _checkbox(selected[index])
        style = TUI_THEME["selection"] if index == cursor else ""
        table.add_row(checkbox, provider, style=style)
    console.print(
        Panel(
            table,
            title="First run: choose providers",
            subtitle=_footer_for("first_run"),
        )
    )


def _render(
    console: Console,
    workflow_name: str,
    prompt: str,
    options: list[StageOption],
    selected: list[bool],
    cursor: int,
) -> None:
    console.clear()
    capacity = _stage_configuration_row_capacity(console)
    window = _window_options(list(enumerate(options)), cursor, capacity)
    visible_rows, window_start, window_end = window
    table = _ui_table(row_styles=["", TUI_THEME["muted"]])
    table.add_column("Run", width=5, justify="center", no_wrap=True)
    table.add_column("Stage", style="bold", no_wrap=True, overflow="ellipsis")
    table.add_column("Tool", no_wrap=True, overflow="ellipsis")
    table.add_column("Prompt", no_wrap=True, overflow="ellipsis")

    for index, option in visible_rows:
        checkbox = _checkbox(selected[index])
        style = TUI_THEME["selection"] if index == cursor else ""
        table.add_row(checkbox, escape(option.stage_id), escape(option.tool), escape(option.prompt), style=style)

    subtitle = _footer_for("workflow")
    if window_end - window_start < len(options):
        subtitle += f" Showing {window_start + 1}-{window_end} of {len(options)}."
    console.print(
        Panel(
            table,
            title=f"CLI-Router: {workflow_name}",
            subtitle=subtitle,
        )
    )


def _run_stage_configuration_screen(
    config: RouterConfig,
    workflow_name: str,
    console: Console,
    read_key: KeyReader,
    persistent: bool,
) -> int | None:
    cursor = 0
    while True:
        if _render_if_too_small(console):
            key = _read_tui_key(read_key)
            if key in {"q", "Q"}:
                return 0
            if key in {"b", "B", "\x1b"}:
                return None
            continue
        options = stage_options_for_workflow(config, workflow_name)
        if options:
            cursor %= len(options)
        else:
            cursor = 0
        _render_stage_configuration(console, config, workflow_name, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return 0
        if key == "?":
            result = _run_help_screen(console, read_key, current_context="stage_config")
            if result is not None:
                return result
            continue
        if options and key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(options))
            continue
        if options and key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(options))
            continue
        if options and key in {"g", "h", "H"}:
            cursor = _cursor_top(len(options))
            continue
        if options and key in {"G", "l", "L"}:
            cursor = _cursor_bottom(len(options))
            continue
        if key in {"i", "I"}:
            template = _run_stage_library_picker(config, workflow_name, console, read_key)
            if template is None:
                continue
            insert_at = _insert_index_after_option(config, workflow_name, options[cursor] if options else None)
            stage_id = insert_stage(config, workflow_name, insert_at, template)
            cursor = _workflow_stage_index(config, workflow_name, stage_id)
            _persist_if_needed(config, persistent)
            continue
        if options and key in {"x", "X"}:
            remove_stage(config, workflow_name, cursor)
            _persist_if_needed(config, persistent)
            cursor = max(0, min(cursor, len(config.workflows[workflow_name].get("stages", [])) - 1))
            continue
        if key in {"a", "A"}:
            stage_id_value = _run_text_input(console, "Stage name", read_key)
            if stage_id_value is None:
                continue
            stage_id = stage_id_value.strip()
            if not stage_id:
                continue
            stage_prompt_value = _run_prompt_input(console, read_key)
            if stage_prompt_value is None:
                continue
            stage_prompt = stage_prompt_value.strip()
            model_config = _run_model_config_picker(config, _default_model_config(config), console, read_key)
            try:
                add_session_stage(
                    config,
                    workflow_name,
                    stage_id,
                    stage_prompt or "[user prompt]",
                    model_config=model_config,
                )
                _persist_if_needed(config, persistent)
            except PromptVariableError as exc:
                console.print(f"[{TUI_THEME['error']}]{exc}[/]")
            except KeyError as exc:
                console.print(f"[{TUI_THEME['error']}]{exc}[/]")
        if options and key in {"e", "E", "\r", "\n"}:
            selected = options[cursor]
            selected_prompt = next(
                (
                    stage.get("input_template", "{user_prompt}")
                    for stage in config.workflows[workflow_name].get("stages", [])
                    if stage["id"] == selected.stage_id
                ),
                "{user_prompt}",
            )
            stage_prompt_value = _run_prompt_input(
                console,
                read_key,
                initial=_prompt_template_for_edit(str(selected_prompt)),
            )
            if stage_prompt_value is None:
                continue
            stage_prompt = stage_prompt_value.strip()
            model_config = _run_model_config_picker(config, selected.tool, console, read_key) or selected.tool
            try:
                if stage_prompt:
                    set_stage_prompt(config, workflow_name, selected.stage_id, stage_prompt)
                set_stage_model_config(config, workflow_name, selected.stage_id, model_config)
                _persist_if_needed(config, persistent)
            except PromptVariableError as exc:
                console.print(f"[{TUI_THEME['error']}]{exc}[/]")
            except KeyError as exc:
                console.print(f"[{TUI_THEME['error']}]{exc}[/]")
        if options and key in {"m", "M"}:
            selected = options[cursor]
            model_config = _run_model_config_picker(config, selected.tool, console, read_key)
            if model_config is None:
                continue
            try:
                set_stage_model_config(config, workflow_name, selected.stage_id, model_config)
                _persist_if_needed(config, persistent)
            except KeyError as exc:
                console.print(f"[{TUI_THEME['error']}]{exc}[/]")


def _render_stage_configuration(console: Console, config: RouterConfig, workflow_name: str, cursor: int) -> None:
    console.clear()
    options = stage_options_for_workflow(config, workflow_name)
    visible_options, window_start, window_end = _window_options(options, cursor, _stage_configuration_row_capacity(console))
    table = _ui_table()
    table.add_column("Stage", no_wrap=True, overflow="ellipsis")
    table.add_column("Model Config", no_wrap=True, overflow="ellipsis")
    table.add_column("Prompt", no_wrap=True, overflow="ellipsis")
    if visible_options:
        for index, option in enumerate(visible_options, start=window_start):
            style = TUI_THEME["selection"] if index == cursor else ""
            table.add_row(escape(option.stage_id), escape(option.tool), escape(option.prompt), style=style)
    else:
        table.add_row("No stages", "Press A to add one.", "")
    subtitle = _footer_for("stage_config")
    if window_end - window_start < len(options):
        subtitle += f" Showing {window_start + 1}-{window_end} of {len(options)}."
    console.print(
        Group(
            Panel(
                table,
                title="Stage configuration",
                subtitle=subtitle,
            ),
            _stage_configuration_action_footer(),
        )
    )


def _stage_configuration_action_footer() -> str:
    return _action_footer_for(
        "stage_config",
        "Add a stage",
        "Insert from library",
        "Remove selected stage",
        "Change model config",
        "Go back",
        "Quit",
        "Help",
    )


def _stage_configuration_row_capacity(console: Console) -> int:
    fixed_rows = 8
    return max(1, console.size.height - fixed_rows)


def _window_options(options: list[T], cursor: int, capacity: int) -> tuple[list[T], int, int]:
    if not options:
        return [], 0, 0
    capacity = max(1, min(capacity, len(options)))
    cursor = min(max(cursor, 0), len(options) - 1)
    start = min(max(cursor - capacity // 2, 0), len(options) - capacity)
    end = start + capacity
    return options[start:end], start, end


def _default_model_config(config: RouterConfig) -> str:
    return _enabled_model_config_names(config)[0]


def _enabled_model_config_names(config: RouterConfig) -> list[str]:
    return [name for name, tool in config.tools.items() if tool.get("enabled", True) is not False]


def _stage_library_templates(config: RouterConfig, workflow_name: str) -> list[dict[str, Any]]:
    templates = config.data.get("stage_library")
    if templates is None:
        templates = config.workflows.get(workflow_name, {}).get("stages", [])
    return [deepcopy(template) for template in templates]


def _run_stage_library_picker(
    config: RouterConfig,
    workflow_name: str,
    console: Console,
    read_key: KeyReader,
) -> dict[str, Any] | None:
    templates = _stage_library_templates(config, workflow_name)
    options = stage_library_options(config, workflow_name)
    if not options:
        console.print(f"[{TUI_THEME['error']}]No stage library templates are configured.[/]")
        return None
    cursor = 0
    while True:
        if _render_if_too_small(console):
            key = _read_tui_key(read_key)
            if key in {"b", "B", "\x1b", "q", "Q"}:
                return None
            continue
        _render_stage_library_picker(console, options, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return None
        if key == "?":
            result = _run_help_screen(console, read_key, current_context="picker")
            if result is not None:
                return None
            continue
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(options))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(options))
            continue
        if key in {"g", "h", "H"}:
            cursor = _cursor_top(len(options))
            continue
        if key in {"G", "l", "L"}:
            cursor = _cursor_bottom(len(options))
            continue
        if key in {"\r", "\n"}:
            return templates[cursor]


def _render_stage_library_picker(console: Console, options: list[StageOption], cursor: int) -> None:
    console.clear()
    visible_options, window_start, window_end = _window_options(options, cursor, _picker_row_capacity(console))
    table = _ui_table()
    table.add_column("Stage", no_wrap=True, overflow="ellipsis")
    table.add_column("Model Config", no_wrap=True, overflow="ellipsis")
    table.add_column("Prompt", no_wrap=True, overflow="ellipsis")
    for index, option in enumerate(visible_options, start=window_start):
        style = TUI_THEME["selection"] if index == cursor else ""
        table.add_row(escape(option.stage_id), escape(option.tool), escape(option.prompt), style=style)
    subtitle = _footer_for("picker")
    if window_end - window_start < len(options):
        subtitle += f" Showing {window_start + 1}-{window_end} of {len(options)}."
    console.print(Panel(table, title="Insert stage", subtitle=subtitle))


def _run_model_config_picker(
    config: RouterConfig,
    current_model_config: str,
    console: Console,
    read_key: KeyReader,
) -> str | None:
    options = _enabled_model_config_names(config)
    if current_model_config not in options and current_model_config in config.tools:
        options.insert(0, current_model_config)
    if not options:
        return None
    cursor = options.index(current_model_config) if current_model_config in options else 0
    while True:
        _render_option_picker(console, "Select model config", options, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return None
        if key == "?":
            result = _run_help_screen(console, read_key, current_context="picker")
            if result is not None:
                return None
            continue
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(options))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(options))
            continue
        if key in {"g", "h", "H"}:
            cursor = _cursor_top(len(options))
            continue
        if key in {"G", "l", "L"}:
            cursor = _cursor_bottom(len(options))
            continue
        if key in {"\r", "\n"}:
            return options[cursor]


def _run_model_config_screen(config: RouterConfig, console: Console, read_key: KeyReader, persistent: bool) -> int | None:
    cursor = 0
    while True:
        if _render_if_too_small(console):
            key = _read_tui_key(read_key)
            if key in {"q", "Q"}:
                return 0
            if key in {"b", "B", "\x1b"}:
                return None
            continue
        options = model_configs_for_config(config)
        if options:
            cursor %= len(options)
        else:
            cursor = 0
        _render_model_config_screen(options, console, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return 0
        if key == "?":
            result = _run_help_screen(console, read_key, current_context="model_config")
            if result is not None:
                return result
            continue
        if options and key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(options))
            continue
        if options and key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(options))
            continue
        if options and key in {"g", "h", "H"}:
            cursor = _cursor_top(len(options))
            continue
        if options and key in {"G", "l", "L"}:
            cursor = _cursor_bottom(len(options))
            continue
        if key in {"a", "A"}:
            current_provider = options[cursor].provider if options else PROVIDERS[0]
            provider = _run_provider_picker(current_provider, console, read_key)
            if provider is None:
                continue
            model = _run_model_picker(provider, console, read_key)
            if model is None:
                continue
            effort = _run_effort_picker(console, read_key)
            if effort is None:
                continue
            default_name = _suggest_model_config_name(config, provider)
            name_value = _run_text_input(console, "Model config name", read_key, initial=default_name)
            if name_value is None:
                continue
            name = name_value.strip()
            if not name:
                continue
            try:
                add_model_config(config, name, provider=provider, model=model, effort=effort)
                _persist_if_needed(config, persistent)
            except KeyError as exc:
                console.print(f"[{TUI_THEME['error']}]{exc}[/]")
            continue
        if options and key in {"e", "E"}:
            selected = options[cursor]
            provider = _run_provider_picker(selected.provider, console, read_key) or selected.provider
            model = _run_model_picker(provider, console, read_key) or selected.model
            effort = _run_effort_picker(console, read_key) or selected.effort
            update_model_config(config, selected.name, provider=provider, model=model, effort=effort)
            _persist_if_needed(config, persistent)


def _suggest_model_config_name(config: RouterConfig, provider: str) -> str:
    if provider not in config.tools:
        return provider
    suffix = 2
    while f"{provider}-{suffix}" in config.tools:
        suffix += 1
    return f"{provider}-{suffix}"


def _render_model_config_screen(options: list[ModelConfigOption], console: Console, cursor: int) -> None:
    console.clear()
    visible_options, window_start, window_end = _window_options(options, cursor, _stage_configuration_row_capacity(console))
    table = _ui_table()
    table.add_column("Model Config", no_wrap=True, overflow="ellipsis")
    table.add_column("Provider", no_wrap=True, overflow="ellipsis")
    table.add_column("Model", no_wrap=True, overflow="ellipsis")
    table.add_column("Effort", no_wrap=True, overflow="ellipsis")
    if visible_options:
        for index, option in enumerate(visible_options, start=window_start):
            style = TUI_THEME["selection"] if index == cursor else ""
            table.add_row(
                escape(option.name),
                escape(option.provider),
                escape(option.model),
                escape(option.effort),
                style=style,
            )
    else:
        table.add_row("No model configs", "Press A to add one.", "", "")
    subtitle = _footer_for("model_config")
    if window_end - window_start < len(options):
        subtitle += f" Showing {window_start + 1}-{window_end} of {len(options)}."
    console.print(
        Group(
            Panel(table, title="Model Config", subtitle=subtitle),
            _action_footer_for("model_config", "Add a model config", "Edit model config", "Go back", "Quit", "Help"),
        )
    )


def _run_provider_picker(current_provider: str, console: Console, read_key: KeyReader) -> str | None:
    providers = list(PROVIDERS)
    if current_provider not in providers and current_provider not in {"<unset>", "generic"}:
        providers.insert(0, current_provider)
    cursor = providers.index(current_provider) if current_provider in providers else 0
    while True:
        _render_option_picker(console, "Select provider", providers, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return None
        if key == "?":
            result = _run_help_screen(console, read_key, current_context="picker")
            if result is not None:
                return None
            continue
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(providers))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(providers))
            continue
        if key in {"g", "h", "H"}:
            cursor = _cursor_top(len(providers))
            continue
        if key in {"G", "l", "L"}:
            cursor = _cursor_bottom(len(providers))
            continue
        if key in {"\r", "\n"}:
            return providers[cursor]


def _run_model_picker(provider: str, console: Console, read_key: KeyReader) -> str | None:
    models = model_options_for_provider(provider, cache=ModelCache.load())
    cursor = 0
    while True:
        _render_option_picker(console, f"Select model for {provider}", models, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return None
        if key == "?":
            result = _run_help_screen(console, read_key, current_context="picker")
            if result is not None:
                return None
            continue
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(models))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(models))
            continue
        if key in {"g", "h", "H"}:
            cursor = _cursor_top(len(models))
            continue
        if key in {"G", "l", "L"}:
            cursor = _cursor_bottom(len(models))
            continue
        if key in {"\r", "\n"}:
            return models[cursor]


def _run_effort_picker(console: Console, read_key: KeyReader) -> str | None:
    efforts = ["low", "medium", "high"]
    cursor = 1
    while True:
        _render_option_picker(console, "Select effort", efforts, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return None
        if key == "?":
            result = _run_help_screen(console, read_key, current_context="picker")
            if result is not None:
                return None
            continue
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(efforts))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(efforts))
            continue
        if key in {"g", "h", "H"}:
            cursor = _cursor_top(len(efforts))
            continue
        if key in {"G", "l", "L"}:
            cursor = _cursor_bottom(len(efforts))
            continue
        if key in {"\r", "\n"}:
            return efforts[cursor]


def _render_option_picker(console: Console, title: str, options: list[str], cursor: int) -> None:
    console.clear()
    visible_options, window_start, window_end = _window_options(options, cursor, _picker_row_capacity(console))
    table = _ui_table(show_header=False)
    table.add_column("Option", no_wrap=True, overflow="ellipsis")
    for index, option in enumerate(visible_options, start=window_start):
        style = TUI_THEME["selection"] if index == cursor else ""
        table.add_row(escape(option), style=style)
    subtitle = _footer_for("picker")
    if window_end - window_start < len(options):
        subtitle += f" Showing {window_start + 1}-{window_end} of {len(options)}."
    console.print(Panel(table, title=title, subtitle=subtitle))


def _picker_row_capacity(console: Console) -> int:
    fixed_rows = 6
    return max(1, console.size.height - fixed_rows)


def _prompt_variable_table() -> Table:
    table = _ui_table()
    table.add_column("Prompt variable")
    table.add_column("Meaning")
    for display_token, (_placeholder, description) in OFFICIAL_PROMPT_VARIABLES.items():
        table.add_row(escape(display_token), description)
    return table


class _TuiObserver:
    _STDOUT_TAIL = 8
    _STDERR_TAIL = 5

    def __init__(self, console: Console) -> None:
        self.console = console
        self._statuses: list[dict[str, str | int]] = []
        self._condensers: dict[tuple[str, str, int], OutputCondenser] = {}
        self._progress: dict[tuple[str, str, int], deque[str]] = {}
        self._current: tuple[str, str, int] | None = None
        self._live: Live | None = None

    def live_context(self):
        if not self.console.is_terminal:
            return nullcontext()
        self._live = Live(self._render(), console=self.console, auto_refresh=False, transient=False)
        return self._live

    def stage_started(self, stage_id: str, tool: str, attempt: int) -> None:
        self._current = (stage_id, tool, attempt)
        self._condensers[self._current] = OutputCondenser()
        self._progress[self._current] = deque(maxlen=200)
        self._statuses.append(
            {"stage": stage_id, "tool": tool, "attempt": attempt, "status": "running", "result": ""}
        )
        if not self.console.is_terminal:
            self.console.print(f"Running {stage_id} ({tool})")
        self._refresh()

    def stage_output(self, stage_id: str, tool: str, line: str) -> None:
        key = (stage_id, tool, self._attempt_for(stage_id, tool))
        condenser = self._condensers.get(key)
        if condenser is None:
            return
        condenser.feed(line)
        self._refresh()

    def stage_error(self, stage_id: str, tool: str, line: str) -> None:
        # Providers such as Codex stream their live progress on stderr; surface
        # it as dimmed secondary output so a long stage is not a silent wait.
        key = (stage_id, tool, self._attempt_for(stage_id, tool))
        progress = self._progress.get(key)
        if progress is None:
            return
        cleaned = strip_ansi(line).rstrip("\n")
        if not cleaned.strip():
            return
        progress.append(cleaned)
        self._refresh()

    def stage_finished(self, stage: StageSummary) -> None:
        key = (stage.stage_id, stage.tool, self._attempt_for(stage.stage_id, stage.tool))
        teaser = first_meaningful_line(stage.extracted)
        for status in reversed(self._statuses):
            if status["stage"] == stage.stage_id and status["tool"] == stage.tool:
                status["status"] = f"exit {stage.result.returncode}"
                status["result"] = teaser
                break
        if not self.console.is_terminal:
            for line in self._condensers.get(key, OutputCondenser()).lines:
                self.console.print(line)
            self.console.print(f"Finished {stage.stage_id} ({stage.tool}) exit {stage.result.returncode}")
        self._refresh()

    def _attempt_for(self, stage_id: str, tool: str) -> int:
        for status in reversed(self._statuses):
            if status["stage"] == stage_id and status["tool"] == tool:
                return int(status["attempt"])
        return 1

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def _render(self) -> Panel:
        muted = TUI_THEME["muted"]
        table = _ui_table()
        table.add_column("Stage", no_wrap=True, overflow="ellipsis")
        table.add_column("Model Config", no_wrap=True, overflow="ellipsis")
        table.add_column("Status", no_wrap=True, overflow="ellipsis")
        table.add_column("Result", no_wrap=True, overflow="ellipsis")
        for status in self._statuses:
            running = status["status"] == "running"
            result = str(status["result"]) or ("working…" if running else "")
            result_cell = f"[{muted}]{escape(result)}[/]" if result else ""
            table.add_row(
                str(status["stage"]),
                str(status["tool"]),
                str(status["status"]),
                result_cell,
            )

        output = _ui_table(show_header=False)
        output.add_column("Output", no_wrap=True, overflow="ellipsis")
        stdout_lines = self._condensers[self._current].lines[-self._STDOUT_TAIL :] if self._current else []
        progress_lines = list(self._progress.get(self._current, deque()))[-self._STDERR_TAIL :] if self._current else []
        if not stdout_lines and not progress_lines:
            output.add_row(f"[{muted}]Waiting for output…[/]")
        else:
            for line in stdout_lines:
                output.add_row(escape(line))
            for line in progress_lines:
                output.add_row(f"[{muted}]· {escape(line)}[/]")

        wrapper = Table.grid(expand=True)
        wrapper.add_row(table)
        wrapper.add_row(output)
        return Panel(wrapper, title="Running workflow", subtitle="Condensed live output · dim = progress")


def _read_key() -> str:
    if os.name == "nt":
        return _read_windows_key()
    return _read_posix_key()


def _read_posix_key() -> str:
    import termios

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    state = _RawTerminalState(fd=fd, previous=previous)
    try:
        _activate_raw_terminal(state)
        # Read straight from the file descriptor with os.read rather than
        # sys.stdin.read: the latter is a buffered TextIOWrapper that can pull a
        # whole "\x1b[A" burst into its internal buffer while returning only the
        # leading Esc, leaving the select() in _read_escape_sequence blind to the
        # remaining bytes and truncating arrow keys to a bare Esc.
        first = _read_utf8_char_or_resize(fd)
        if first == RESIZE_REDRAW_KEY:
            return first
        if first == "\x1b":
            return _read_escape_sequence(fd, lambda: os.read(fd, 1).decode("latin-1"), first)
        return first
    finally:
        _restore_raw_terminal(state)
        _clear_active_raw_terminal(state)


def _activate_raw_terminal(state: _RawTerminalState) -> None:
    import termios
    import tty

    global _ACTIVE_RAW_TERMINAL
    tty.setraw(state.fd, when=termios.TCSANOW)
    state.active = True
    _ACTIVE_RAW_TERMINAL = state


def _restore_raw_terminal(state: _RawTerminalState) -> None:
    import termios

    if not state.active:
        return
    termios.tcsetattr(state.fd, termios.TCSADRAIN, state.previous)
    state.active = False


def _restore_active_raw_terminal() -> None:
    if _ACTIVE_RAW_TERMINAL is not None:
        _restore_raw_terminal(_ACTIVE_RAW_TERMINAL)


def _resume_active_raw_terminal() -> None:
    import termios
    import tty

    if _ACTIVE_RAW_TERMINAL is None or _ACTIVE_RAW_TERMINAL.active:
        return
    tty.setraw(_ACTIVE_RAW_TERMINAL.fd, when=termios.TCSANOW)
    _ACTIVE_RAW_TERMINAL.active = True


def _clear_active_raw_terminal(state: _RawTerminalState) -> None:
    global _ACTIVE_RAW_TERMINAL
    if _ACTIVE_RAW_TERMINAL is state:
        _ACTIVE_RAW_TERMINAL = None


def _read_utf8_char_or_resize(fd: int) -> str:
    import select

    while True:
        if _consume_resize_redraw():
            return RESIZE_REDRAW_KEY
        if select.select([fd], [], [], 0.1)[0]:
            return _read_utf8_char(fd)


def _read_utf8_char(fd: int) -> str:
    first = os.read(fd, 1)
    if not first:
        return ""
    lead = first[0]
    if lead < 0x80:
        return first.decode("latin-1")
    if lead >= 0xF0:
        continuation = 3
    elif lead >= 0xE0:
        continuation = 2
    elif lead >= 0xC0:
        continuation = 1
    else:
        continuation = 0
    buffer = bytearray(first)
    for _ in range(continuation):
        buffer += os.read(fd, 1)
    return buffer.decode("utf-8", "replace")


def _read_escape_sequence(fd: int, read_char: Callable[[], str], first: str, timeout: float = 0.05) -> str:
    import select

    sequence = first
    if not select.select([fd], [], [], timeout)[0]:
        return sequence
    while select.select([fd], [], [], 0)[0]:
        char = read_char()
        if not char:
            break
        sequence += char
    return sequence


def _read_windows_key() -> str:
    import msvcrt

    first = msvcrt.getwch()
    if first in {"\x00", "\xe0"}:
        second = msvcrt.getwch()
        if second == "H":
            return "\x1b[A"
        if second == "P":
            return "\x1b[B"
    return first


def _print_summary(console: Console, summary: WorkflowSummary) -> None:
    console.print()
    console.print(f"run_dir: {summary.run_dir}")
    console.print(f"plan_path: {summary.plan_path}")
    for stage in summary.stages:
        console.print(f"{stage.stage_id}: exit {stage.result.returncode}")
        _print_stage_output(console, "stdout", stage.result.stdout)
        _print_stage_output(console, "stderr", stage.result.stderr)
        if stage.extracted is not None and stage.extracted != stage.result.stdout:
            _print_stage_output(console, "extracted", stage.extracted)
    if summary.error:
        console.print(f"[{TUI_THEME['error']}]error: {summary.error}[/]")
    console.print(f"exit_code: {summary.exit_code}")


def _print_compact_summary(console: Console, summary: WorkflowSummary) -> None:
    console.print()
    table = _ui_table()
    table.add_column("Stage", no_wrap=True, overflow="ellipsis")
    table.add_column("Model Config", no_wrap=True, overflow="ellipsis")
    table.add_column("Exit", justify="right")
    for stage in summary.stages:
        table.add_row(escape(stage.stage_id), escape(stage.tool), str(stage.result.returncode))
    console.print(Panel(table, title="Workflow summary"))

    for stage in summary.stages:
        preview = condense_extracted(stage.extracted)
        if not preview:
            continue
        console.print(
            f"[{TUI_THEME['title']}]{escape(stage.stage_id)}[/] "
            f"[{TUI_THEME['muted']}]({escape(stage.tool)})[/]"
        )
        console.print(escape(preview))
        console.print()

    console.print(f"run_dir: {summary.run_dir}")
    console.print(f"plan_path: {summary.plan_path}")
    console.print(f"Full output saved to {summary.run_dir}")
    if summary.error:
        console.print(f"[{TUI_THEME['error']}]error: {summary.error}[/]")
    console.print(f"exit_code: {summary.exit_code}")


def _print_stage_output(console: Console, label: str, output: str) -> None:
    if not output:
        return
    console.print(f"{label}:")
    console.print(output.rstrip("\n"))
