# Changelog

## Unreleased

## 0.3.0 - 2026-07-13

- Added a `doctor` feature (`cli-router doctor`, `--repair`) to catch agent-CLI drift over time. `doctor` reports each provider's discovery health (CLI present? live model list parses?). With `--repair`, when a provider's CLI is installed and responding but its model list no longer parses, Doctor uses a working agent as an LLM parser — it runs the sick provider's own list command itself, hands only the raw text to the agent, and expects back a JSON array of model ids (LLM-as-parser, never LLM-as-operator). Recovered lists are written to `~/.cli-router/model-cache.yaml` (`cli_router.modelcache.ModelCache`), which layers between live discovery and the static `DEFAULT_MODELS` fallback, so the fix survives across runs without editing source. Model discovery and the TUI model pickers now consult that cache.
- Made Doctor resilient to broken agents via a modular backend-failover chain. Instead of trusting one "healthy" provider, Doctor builds an ordered chain of `(provider, model)` backends — installed providers alphabetically, each provider's models cache-first then `DEFAULT_MODELS` — and walks it until one actually answers, pinning the winner and reusing it for the remaining repairs. As long as one provider+model works, Doctor can heal the rest. A stale model (e.g. a retired `gpt-5`) simply fails and falls through to the next candidate. The LLM call (`run_agent`) is an isolated seam so future Doctor tasks can reuse the same selection logic. Repairs are cancellable (a `cancelled` hook, or Ctrl-C), stopping gracefully with partial results preserved.
- Lengthened Doctor's discovery timeout to 10s (vs. the interactive picker's 1.5s, now a `probe_models(..., timeout=)` parameter) so slow-cold-starting CLIs are diagnosed instead of timing out.
- Stopped probing providers that have no safe, machine-readable model-list command. `claude models` hangs and `claude model list` would start a **billable agent turn**; `hermes model` is an interactive login/selector. These are removed from discovery, so Claude and Hermes now resolve straight to their static/cached model lists without ever shelling out, and Doctor reports them honestly as `static` rather than crying `drift`. (Codex and Grok keep live discovery.)
- Hardened `_parse_model_catalog` to decode the JSON object at each `{` (via `raw_decode`), tolerating a banner before the catalog and a log line printed after it — the exact format-drift Doctor exists to weather.
- Hardened Grok/text model-list parsing against banner drift: `_parse_model_output` is now section-aware (only trusts a `Default model:` line and entries under `Available models:`, falling back to scanning all lines only when there is no header) and requires each token to look like a model id (no spaces, carries a digit or `-`). This replaces the old prefix-denylist noise filter, which leaked banner words like `You` when the login/status wording drifted — a latent glitch that became load-bearing once the model is passed as `-m`.
- Routed the selected model **and reasoning effort** to the underlying CLI: `provider_tool_config` now bakes both into the command (`codex exec -c model_reasoning_effort=<effort> -m <model>`, `claude -p --model <model> --effort <effort>`, `grok --single -m <model> --reasoning-effort <effort>`), and editing a provider model config in the TUI regenerates the command. Previously the `model` and `effort` fields were stored but never passed, so every model config ran the tool's default model at its default effort. Custom/generic tools keep their hand-written commands; Hermes (single auto-routing model) takes neither flag.
- Refreshed the Codex static fallback model list to the current catalog (`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`), replacing the retired `gpt-5.1`/`gpt-5` slugs. Live `codex debug models` discovery already surfaces the GPT-5.6 Sol/Terra/Luna models; the Claude list already includes `claude-fable-5`.
- Added per-stage output between stages instead of a bare `exit 0`: the plain CLI and the TUI end-of-run summary now print each stage's extracted answer condensed to a half-page preview, and the TUI live view shows a one-line result teaser per finished stage.
- Streamed provider progress live: `stream_tool` now delivers stderr lines through a new `on_stderr_line` callback (both streams funnelled through one dispatch loop), and the TUI renders that progress as dimmed, ANSI-stripped secondary output — so long stages (e.g. Codex, which streams to stderr) are no longer a silent wait.
- Added `condense_extracted`, `first_meaningful_line`, and `strip_ansi` helpers in `streamfmt`.
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
