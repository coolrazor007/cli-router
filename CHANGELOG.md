# Changelog

## Unreleased

- Added `grok` as a built-in provider: default model list, `grok models` discovery, a `grok --single` command template, a packaged `grok.yaml` preset, and ANSI/noise stripping in model-list parsing so discovery output stays clean.
- Added `stage_library` config validation: it must be a list of mappings, each with `id`, `tool`, and `input_template`, referencing a known tool.
- Made `[previous stage output]` a real variable: it now injects the immediately preceding stage's final extracted text (`{previous_output}`), not the plan file path. Previously it was aliased to `{plan_path}`, so downstream stages (QA, Summary) never saw prior stage output and could report stale or contradictory results.
- Added `[all stage outputs]` (`{all_stage_outputs}`): every completed stage's final output so far, labeled by stage id — useful for QA and Summary stages.
- Added `[plan file]` (`{plan_path}`) as a distinct variable for referencing the plan file path.
- Slimmed the run manifest (`run.yaml`): per-stage records no longer embed full `stdout`/`stderr` (already saved as sidecar `.stdout`/`.stderr` files). Each stage now records byte counts plus the artifact filenames, keeping the rendered `command` for diagnostics. A run that dumped ~800 KB of duplicated streams now writes a ~4 KB manifest. `runs show` reads the flattened `exit_code` and still renders older manifests.
- Updated packaged presets and first-run TUI seed prompts: planner and QA stages now instruct "do not edit any files; respond only in Markdown", coder references the plan file, QA reviews the previous stage's output, and Summary consumes all stage outputs.
- Fixed TUI stage reordering (`U`/`D`) so the new order is written to the workflow config instead of only the on-screen list; reordered stages now run in the chosen order and survive add/remove and plain `cli-router run`.
- Persisted TUI edits back to whichever config is in use — a project-local `cli-router.yaml` or an explicit `--config` path, not just the home config — so reorders, stage add/remove, and Model Config changes survive across runs.

## 0.2.0

- Added modular workflow stages.
- Added a `stage_library` config section for reusable TUI stage templates.
- Added TUI stage-library insertion and selected-stage removal from Workflow and Stage configuration screens.
- Added duplicate stage insertion with unique auto-suffixed ids such as `coder-2`.
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
- Added Grok provider support, including first-run TUI selection, `grok models` discovery, and a `grok --single` preset.
- Added Model Config menu support for adding providers after first-run onboarding.
- Prevented accidental Enter on Model Config from entering edit mode.
- Added a multiline Stage Configuration prompt editor that keeps prompt variables visible and saves with Ctrl+D.
- Made provider model discovery close stdin and use a short timeout before falling back.
- Changed Codex model discovery to parse `codex debug models` so newly available Codex models are selectable without a package update.
- Updated the static Claude fallback model list to current Claude Code model IDs: Fable 5, Opus 4.8, Sonnet 5, and Haiku 4.5.
- Made Ctrl+C cancel immediately from TUI screens, nested pickers, and prompt input with exit code `130`.
- Added `cli-router runs` and `cli-router runs show <id>` to inspect saved run artifacts.
- Added persistent diagnostics under `~/.cli-router/logs/`, including rotating workflow logs, JSONL run metrics, and stage duration metrics in `run.yaml`.

## 0.1.0

- Initial PyPI-ready CLI-Router MVP.
