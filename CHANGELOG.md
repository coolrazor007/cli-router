# Changelog

## 0.2.0

- Added modular workflow stages.
- Added `enabled: false` for opt-in stages.
- Added `--stages` to run explicit stage selections in explicit order.
- Added `cli-router tui` for interactive stage selection, toggling, and ordering.
- Made bare `cli-router` open the TUI by default.
- Changed bare `cli-router` to open the TUI main menu before prompting.
- Changed the TUI Prompt menu item to ask for a prompt and immediately run the enabled workflow.
- Added a TUI main menu with Workflow, Stage configuration, and Model and providers sections.
- Replaced the workflow State column with stage Prompt previews and Unicode checkbox selectors.
- Moved prompt editing into its own TUI menu section and added back navigation from Workflow.
- Moved Prompt to the top of the TUI main menu.
- Added official TUI prompt variables and validation for `[user prompt]` and `[previous stage output]`.
- Reworded Stage configuration controls to explicit “Press A...” style instructions.
- Standardized TUI navigation: `B` is back and `Q` is quit.
- Renamed TUI tool/provider display to Model Config and added editable provider/model/effort metadata.
- Linked stage configuration to Model Config entries instead of raw tool labels.
- Added row selection and Model Config pickers to Stage Configuration so users no longer type stage IDs or model config names from memory.
- Made Esc cancel TUI text entry and Stage Configuration add/edit flows without applying partial changes.
- Added immediate "Running workflow" feedback when Prompt or Workflow starts external tool execution.
- Stream condensed stage output in the TUI and collapse verbose thinking/diff output by default.
- Added `defaults.tui_verbosity: full` to restore raw TUI stdout/stderr summaries when needed.
- Added `~/.cli-router/config.yaml` persistence for first-run setup and TUI edits.
- Added `auth_required` failure classification and included provider error snippets in nonzero stage failure messages.
- Added first-run provider selection for `codex`, `claude`, and `hermes`.
- Added provider model discovery through provider CLIs with built-in fallbacks.
- Prevented accidental Enter on Model Config from entering edit mode.
- Made provider model discovery close stdin and use a short timeout before falling back.
- Changed Codex model discovery to parse `codex debug models` so newly available Codex models are selectable without a package update.
- Updated the static Claude fallback model list to current Claude Code model IDs: Fable 5, Opus 4.8, Sonnet 5, and Haiku 4.5.
- Made Ctrl+C cancel immediately from TUI screens, nested pickers, and prompt input with exit code `130`.
- Added `cli-router runs` and `cli-router runs show <id>` to inspect saved run artifacts.
- Added persistent diagnostics under `~/.cli-router/logs/`, including rotating workflow logs, JSONL run metrics, and stage duration metrics in `run.yaml`.

## 0.1.0

- Initial PyPI-ready CLI-Router MVP.
