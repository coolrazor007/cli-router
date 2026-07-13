# Agent loops on a self-hosted runner ("Mothership")

The agent loops (starting with the **drift watchdog**) run on a self-hosted
GitHub Actions runner on your own machine, because the provider CLIs
(`claude`, `codex`, `grok`) are **subscription-authenticated** — their
credentials live in your local `$HOME`, which ephemeral GitHub-hosted runners
can't carry.

> **Security first — this repo is public.** A self-hosted runner on a public
> repo is dangerous: fork pull requests could run arbitrary code on the machine
> that holds your subscription logins. Everything below is built to *never run
> untrusted code on the runner*. Read [Security model](#security-model) before
> enabling anything.

---

## 1. Prerequisites on Mothership

- `python3` (3.10+), `git`, and the [`gh`](https://cli.github.com/) CLI.
- The provider CLIs installed **and logged in as the runner user**:
  `claude`, `codex`, `grok`.
- Verify headless, non-interactive auth **as the user the runner will run as**:

  ```bash
  claude -p "reply with just: OK"
  codex exec "reply with just: OK"
  grok --single "reply with just: OK"
  ```

  Each must return without prompting for login or a TTY. If any prompts, log in
  first (`claude`, `codex login`, `grok` interactive) — the runner inherits that
  session via `$HOME`.

## 2. Create an isolated runner user (recommended)

Run the runner as a **dedicated user** whose `$HOME` holds *only* the CLI auth
you want exposed — not your personal secrets. Log the provider CLIs in as that
user. This limits blast radius if a workflow is ever abused.

## 3. Register the runner

GitHub → repo **Settings → Actions → Runners → New self-hosted runner**, then
follow the shown download + configure steps. When configuring, **add the label
`mothership`** (the workflows target `runs-on: [self-hosted, mothership]`):

```bash
./config.sh --url https://github.com/coolrazor007/cli-router \
            --token <REGISTRATION_TOKEN> \
            --labels mothership --unattended
./run.sh          # or install as a service: ./svc.sh install && ./svc.sh start
```

If you install it as a service, make sure the **service runs as the
CLI-authenticated user** (so `$HOME` resolves to the logged-in config), e.g.
`./svc.sh install <that-user>`.

## 4. Security model

- **Triggers are trusted-only.** The drift watchdog runs on `schedule` and
  manual `workflow_dispatch` only — never `pull_request`. Do not add fork- or
  PR-triggered jobs to any workflow that uses `runs-on: [self-hosted, ...]`.
- **Require approval for fork PRs.** Settings → Actions → General → *Fork pull
  request workflows from outside collaborators* → **Require approval for all
  external contributors** (and prefer "all fork pull requests").
- **Least privilege.** The workflow requests only `contents`, `pull-requests`,
  and `issues` write. The provider credentials never enter a prompt — the agent
  step only receives the drift report.
- **Isolate.** A dedicated user (above), and ideally a VM/container with limited
  outbound network, so a compromised run can't reach the rest of Mothership.

## 5. Repo configuration (one-time)

Set these as repo **Variables** (Settings → Secrets and variables → Actions →
Variables) — not secrets:

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_DAILY_CAP` | `10` | Runaway breaker: pause all loops if this many agent issues+PRs are created in 24h. |
| `AGENT_DRIFT_AUTOMERGE` | `false` | When `true`, the watchdog merges its own PR after tests pass. Start `false` (PR-only). |

Labels (`agent-halt`, `agent-created`, `agent-drift`) are created automatically
on first run.

## 6. The circuit breaker / kill switch

Two independent brakes, both reversible with only `issues` permission:

- **Kill switch:** if an open issue labelled **`agent-halt`** exists, every
  agent workflow no-ops. To **pause** immediately, open one
  (`gh issue create --label agent-halt --title "pause agents"`). To **resume**,
  close it.
- **Runaway cap:** `scripts/agent/preflight.py` counts `agent-created`
  issues+PRs in the last 24h; at/over `AGENT_DAILY_CAP` it opens the
  `agent-halt` issue automatically and pauses everything until you review and
  close it.

To hard-stop a single loop, disable its workflow: Actions → *Agent drift
watchdog* → ⋯ → **Disable workflow** (or `gh workflow disable "Agent drift watchdog"`).

## 7. Try it safely

1. **Dry run** (detect only, no changes): Actions → *Agent drift watchdog* →
   Run workflow → `dry_run: true`. Confirm the runner picks it up and the
   detector prints a verdict.
2. **Live, PR-only** (`AGENT_DRIFT_AUTOMERGE=false`): Run workflow with
   `dry_run: false`. If drift exists, it opens a PR you review.
3. **Enable auto-merge** only once you trust it: set
   `AGENT_DRIFT_AUTOMERGE=true`.

## What runs where

| Piece | Where | Notes |
|---|---|---|
| `scripts/agent/detect_drift.py` | runner | Deterministic; compares live discovery vs `DEFAULT_MODELS`. |
| `scripts/agent/preflight.py` | runner | Circuit breaker + kill switch (uses `gh`). |
| `scripts/agent/build_prompt.py` | runner | Turns the drift verdict into a bounded prompt. |
| `scripts/agent/apply_drift_fix.sh` | runner | **Tune this** to your local agent CLI/flags. |
| `.github/workflows/agent-drift-watchdog.yml` | orchestration | Trusted triggers only. |
