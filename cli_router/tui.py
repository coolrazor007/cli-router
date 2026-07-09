"""Interactive terminal UI for selecting workflow stages."""

from __future__ import annotations

import os
import re
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .config import RouterConfig, save_config, user_config_path
from .models import PROVIDERS, model_options_for_provider, provider_tool_config
from .streamfmt import OutputCondenser
from .workflows import StageSummary, WorkflowSummary, run_workflow


KeyReader = Callable[[], str]


OFFICIAL_PROMPT_VARIABLES = {
    "[user prompt]": ("{user_prompt}", "Original request entered in the Prompt menu"),
    "[previous stage output]": ("{plan_path}", "Output file from the previous planning/output stage"),
}
PROMPT_VARIABLE_PATTERN = re.compile(r"\[[^\]]+\]")


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

    try:
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
            _render_main_menu(console, workflow_name, prompt, menu_items, cursor)
            key = _read_tui_key(read_key)
            if key in {"q", "Q"}:
                return 0
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
                result = _run_workflow_screen(config, workflow_name, prompt, console, read_key)
                if result is not None:
                    return result
                continue
            if choice == "Prompt":
                prompt = _run_text_input(console, "Prompt", read_key)
                if prompt is None:
                    continue
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
        console.print("Canceled")
        return 130


def _run_workflow_screen(
    config: RouterConfig,
    workflow_name: str,
    prompt: str,
    console: Console,
    read_key: KeyReader,
) -> int | None:
    options = stage_options_for_workflow(config, workflow_name)
    if not options:
        console.print("[red]No stages are configured for this workflow.[/red]")
        return 2

    selected = [option.selected for option in options]
    cursor = 0

    while True:
        _render(console, workflow_name, prompt, options, selected, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return 0
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(options))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(options))
            continue
        if key == " ":
            selected = toggle_selection(selected, cursor)
            continue
        if key in {"u", "U"}:
            options, selected, cursor = move_stage(options, selected, cursor, -1)
            continue
        if key in {"d", "D"}:
            options, selected, cursor = move_stage(options, selected, cursor, 1)
            continue
        if key in {"\r", "\n"}:
            stage_names = selected_stage_names(options, selected)
            if not stage_names:
                console.print("[red]Select at least one stage.[/red]")
                continue
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
    workflow.setdefault("stages", []).append(
        {
            "id": stage_id,
            "tool": tool_name,
            "input_template": normalize_prompt_template(prompt),
            "enabled": True,
        }
    )


def _should_persist(config: RouterConfig) -> bool:
    return config.source is None or config.source.resolve() == user_config_path().resolve()


def _persist_if_needed(config: RouterConfig, persistent: bool) -> None:
    if persistent:
        save_config(config)


def _configure_first_run(config: RouterConfig, providers: list[str]) -> None:
    first_provider = providers[0]
    tools = {
        provider: provider_tool_config(provider, model_options_for_provider(provider)[0])
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
            "workflows": {
                "default": {
                    "stages": [
                        {
                            "id": "planner",
                            "tool": first_provider,
                            "input_template": normalize_prompt_template("Plan this change: [user prompt]"),
                            "output_file": "PLAN.md",
                        },
                        {
                            "id": "coder",
                            "tool": first_provider,
                            "input_template": normalize_prompt_template(
                                "Please implement this: [previous stage output]\n\nOriginal request: [user prompt]"
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


def selected_stage_names(options: list[StageOption], selected: list[bool]) -> list[str]:
    return [option.stage_id for option, is_selected in zip(options, selected) if is_selected]


def prompt_preview(template: str, user_prompt: str) -> str:
    preview = template
    for display_token, (placeholder, _description) in OFFICIAL_PROMPT_VARIABLES.items():
        preview = preview.replace(placeholder, display_token)
    preview = preview.replace("{user_prompt}", user_prompt or "[user prompt]")
    preview = preview.replace("{prompt}", "[stage prompt]")
    return " ".join(preview.split())


def normalize_prompt_template(template: str) -> str:
    normalized = template
    for token in PROMPT_VARIABLE_PATTERN.findall(template):
        if token not in OFFICIAL_PROMPT_VARIABLES:
            raise PromptVariableError(f"Unknown prompt variable: {token}")
        normalized = normalized.replace(token, OFFICIAL_PROMPT_VARIABLES[token][0])
    return normalized


def _move_cursor(cursor: int, delta: int, count: int) -> int:
    return (cursor + delta) % count


def _read_tui_key(read_key: KeyReader) -> str:
    key = read_key()
    if key == "\x03":
        raise KeyboardInterrupt
    return key


def _run_text_input(
    console: Console,
    title: str,
    read_key: KeyReader,
    initial: str = "",
) -> str | None:
    value = initial
    while True:
        _render_text_input(console, title, value)
        key = _read_tui_key(read_key)
        if key == "\x1b":
            return None
        if key in {"\r", "\n"}:
            return value
        if key in {"\x7f", "\b"}:
            value = value[:-1]
            continue
        if len(key) == 1 and key.isprintable():
            value += key


def _render_text_input(console: Console, title: str, value: str) -> None:
    console.clear()
    console.print(
        Panel(
            escape(value or "<empty>"),
            title=title,
            subtitle="Enter saves. Esc cancels. Backspace deletes.",
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
    table = Table(expand=True, show_header=False)
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
    table = Table(expand=True, show_header=False)
    table.add_column("Menu")
    for index, item in enumerate(menu_items):
        style = "bold cyan" if index == cursor else ""
        table.add_row(item, style=style)
    console.print(
        Panel(
            table,
            title="CLI-Router",
            subtitle=f"Workflow: {workflow_name}  Up/Down move  Enter open  Q quit",
        )
    )


def _run_first_run_provider_screen(console: Console, read_key: KeyReader) -> list[str] | None:
    selected = [False for _provider in PROVIDERS]
    cursor = 0
    while True:
        _render_first_run_provider_screen(console, selected, cursor)
        key = _read_tui_key(read_key)
        if key in {"q", "Q", "\x1b"}:
            return None
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
            console.print("[red]Select at least one provider.[/red]")


def _render_first_run_provider_screen(console: Console, selected: list[bool], cursor: int) -> None:
    console.clear()
    table = Table(expand=True, show_header=True, header_style="bold")
    table.add_column("Use", width=5, justify="center", no_wrap=True)
    table.add_column("Provider")
    for index, provider in enumerate(PROVIDERS):
        checkbox = "☑" if selected[index] else "☐"
        style = "bold cyan" if index == cursor else ""
        table.add_row(checkbox, provider, style=style)
    console.print(
        Panel(
            table,
            title="First run: choose providers",
            subtitle="Space toggles. Enter saves ~/.cli-router/config.yaml. Q quits.",
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
    table = Table(expand=True, show_header=True, header_style="bold", row_styles=["", "dim"])
    table.add_column("Run", width=5, justify="center", no_wrap=True)
    table.add_column("Stage", style="bold")
    table.add_column("Tool")
    table.add_column("Prompt")

    for index, option in enumerate(options):
        checkbox = "☑" if selected[index] else "☐"
        style = "bold cyan" if index == cursor else ""
        table.add_row(checkbox, escape(option.stage_id), escape(option.tool), escape(option.prompt), style=style)

    console.print(
        Panel(
            table,
            title=f"CLI-Router: {workflow_name}",
            subtitle="Up/Down cursor  Space toggle  U/D reorder  Enter run  B back  Q quit",
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
        if options and key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(options))
            continue
        if options and key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(options))
            continue
        if key in {"a", "A"}:
            stage_id_value = _run_text_input(console, "Stage name", read_key)
            if stage_id_value is None:
                continue
            stage_id = stage_id_value.strip()
            if not stage_id:
                continue
            stage_prompt_value = _run_text_input(console, "Stage prompt", read_key)
            if stage_prompt_value is None:
                continue
            stage_prompt = stage_prompt_value.strip()
            model_config = _run_model_config_picker(config, _default_model_config(config), console, read_key)
            try:
                add_session_stage(config, workflow_name, stage_id, stage_prompt or "[user prompt]", model_config=model_config)
                _persist_if_needed(config, persistent)
            except PromptVariableError as exc:
                console.print(f"[red]{exc}[/red]")
            except KeyError as exc:
                console.print(f"[red]{exc}[/red]")
        if options and key in {"e", "E", "\r", "\n"}:
            selected = options[cursor]
            stage_prompt_value = _run_text_input(console, "Stage prompt", read_key)
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
                console.print(f"[red]{exc}[/red]")
            except KeyError as exc:
                console.print(f"[red]{exc}[/red]")
        if options and key in {"m", "M"}:
            selected = options[cursor]
            model_config = _run_model_config_picker(config, selected.tool, console, read_key)
            if model_config is None:
                continue
            try:
                set_stage_model_config(config, workflow_name, selected.stage_id, model_config)
                _persist_if_needed(config, persistent)
            except KeyError as exc:
                console.print(f"[red]{exc}[/red]")


def _render_stage_configuration(console: Console, config: RouterConfig, workflow_name: str, cursor: int) -> None:
    console.clear()
    table = Table(expand=True, show_header=True, header_style="bold")
    table.add_column("Stage")
    table.add_column("Model Config")
    table.add_column("Prompt")
    for index, option in enumerate(stage_options_for_workflow(config, workflow_name)):
        style = "bold cyan" if index == cursor else ""
        table.add_row(escape(option.stage_id), escape(option.tool), escape(option.prompt), style=style)
    console.print(
        Panel(
            table,
            title="Stage configuration",
            subtitle="Up/Down select. Enter edits selected stage.",
        )
    )
    console.print("Press A to add a stage. Press M to change model config. Press B to go back. Press Q to quit.")
    console.print(_prompt_variable_table())


def _default_model_config(config: RouterConfig) -> str:
    return _enabled_model_config_names(config)[0]


def _enabled_model_config_names(config: RouterConfig) -> list[str]:
    return [name for name, tool in config.tools.items() if tool.get("enabled", True) is not False]


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
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(options))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(options))
            continue
        if key in {"\r", "\n"}:
            return options[cursor]


def _run_model_config_screen(config: RouterConfig, console: Console, read_key: KeyReader, persistent: bool) -> int | None:
    cursor = 0
    while True:
        options = model_configs_for_config(config)
        _render_model_config_screen(options, console, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return 0
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(options))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(options))
            continue
        if key in {"e", "E"}:
            selected = options[cursor]
            provider = _run_provider_picker(selected.provider, console, read_key) or selected.provider
            model = _run_model_picker(provider, console, read_key) or selected.model
            effort = _run_effort_picker(console, read_key) or selected.effort
            update_model_config(config, selected.name, provider=provider, model=model, effort=effort)
            _persist_if_needed(config, persistent)


def _render_model_config_screen(options: list[ModelConfigOption], console: Console, cursor: int) -> None:
    console.clear()
    table = Table(expand=True, show_header=True, header_style="bold")
    table.add_column("Model Config")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Effort")
    for index, option in enumerate(options):
        style = "bold cyan" if index == cursor else ""
        table.add_row(
            escape(option.name),
            escape(option.provider),
            escape(option.model),
            escape(option.effort),
            style=style,
        )
    console.print(Panel(table, title="Model Config"))
    console.print("Press E to edit model config. Press B to go back. Press Q to quit.")


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
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(providers))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(providers))
            continue
        if key in {"\r", "\n"}:
            return providers[cursor]


def _run_model_picker(provider: str, console: Console, read_key: KeyReader) -> str | None:
    models = model_options_for_provider(provider)
    cursor = 0
    while True:
        _render_option_picker(console, f"Select model for {provider}", models, cursor)
        key = _read_tui_key(read_key)
        if key in {"b", "B", "\x1b"}:
            return None
        if key in {"q", "Q"}:
            return None
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(models))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(models))
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
        if key in {"\x1b[A", "k", "K"}:
            cursor = _move_cursor(cursor, -1, len(efforts))
            continue
        if key in {"\x1b[B", "j", "J"}:
            cursor = _move_cursor(cursor, 1, len(efforts))
            continue
        if key in {"\r", "\n"}:
            return efforts[cursor]


def _render_option_picker(console: Console, title: str, options: list[str], cursor: int) -> None:
    console.clear()
    table = Table(expand=True, show_header=False)
    table.add_column("Option")
    for index, option in enumerate(options):
        style = "bold cyan" if index == cursor else ""
        table.add_row(escape(option), style=style)
    console.print(Panel(table, title=title, subtitle="Up/Down move. Enter selects. B goes back. Q quits."))


def _prompt_variable_table() -> Table:
    table = Table(expand=True, show_header=True, header_style="bold")
    table.add_column("Prompt variable")
    table.add_column("Meaning")
    for display_token, (_placeholder, description) in OFFICIAL_PROMPT_VARIABLES.items():
        table.add_row(escape(display_token), description)
    return table


class _TuiObserver:
    def __init__(self, console: Console) -> None:
        self.console = console
        self._statuses: list[dict[str, str | int]] = []
        self._condensers: dict[tuple[str, str, int], OutputCondenser] = {}
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
        self._statuses.append({"stage": stage_id, "tool": tool, "attempt": attempt, "status": "running"})
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

    def stage_finished(self, stage: StageSummary) -> None:
        key = (stage.stage_id, stage.tool, self._attempt_for(stage.stage_id, stage.tool))
        for status in reversed(self._statuses):
            if status["stage"] == stage.stage_id and status["tool"] == stage.tool:
                status["status"] = f"exit {stage.result.returncode}"
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
        table = Table(expand=True)
        table.add_column("Stage")
        table.add_column("Model Config")
        table.add_column("Attempt", justify="right")
        table.add_column("Status")
        for status in self._statuses:
            table.add_row(
                str(status["stage"]),
                str(status["tool"]),
                str(status["attempt"]),
                str(status["status"]),
            )

        output = Table(expand=True, show_header=False)
        output.add_column("Output")
        if self._current is not None:
            for line in self._condensers.get(self._current, OutputCondenser()).lines[-12:]:
                output.add_row(escape(line))
        else:
            output.add_row("Waiting for first stage...")

        wrapper = Table.grid(expand=True)
        wrapper.add_row(table)
        wrapper.add_row(output)
        return Panel(wrapper, title="Running workflow", subtitle="Condensed live output")


def _read_key() -> str:
    if os.name == "nt":
        return _read_windows_key()
    return _read_posix_key()


def _read_posix_key() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        first = sys.stdin.read(1)
        if first == "\x1b":
            return _read_escape_sequence(fd, lambda: sys.stdin.read(1), first)
        return first
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


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
        console.print(f"[red]error: {summary.error}[/red]")
    console.print(f"exit_code: {summary.exit_code}")


def _print_compact_summary(console: Console, summary: WorkflowSummary) -> None:
    console.print()
    table = Table(expand=True, show_header=True, header_style="bold")
    table.add_column("Stage")
    table.add_column("Model Config")
    table.add_column("Exit", justify="right")
    for stage in summary.stages:
        table.add_row(escape(stage.stage_id), escape(stage.tool), str(stage.result.returncode))
    console.print(Panel(table, title="Workflow summary"))
    console.print(f"run_dir: {summary.run_dir}")
    console.print(f"plan_path: {summary.plan_path}")
    console.print(f"Full output saved to {summary.run_dir}")
    if summary.error:
        console.print(f"[red]error: {summary.error}[/red]")
    console.print(f"exit_code: {summary.exit_code}")


def _print_stage_output(console: Console, label: str, output: str) -> None:
    if not output:
        return
    console.print(f"{label}:")
    console.print(output.rstrip("\n"))
