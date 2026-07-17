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
- The dedicated runner user must **not** have a persistent `gh auth login` or
  GitHub Git credential. The workflow supplies short-lived tokens only to its
  trusted reporting/publishing steps and refuses to start the patch agent when
  a stored `gh` login is detected.
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
- **Least privilege.** Checkout does not persist Git credentials, and GitHub
  tokens are scoped to the preflight and issue-reporting steps. The patch agent
  receives no GitHub or Actions token; it runs on an unpublished local branch,
  and `validate_patch.py` rejects changes outside the three expected files. A
  short-lived patch artifact then crosses into ephemeral GitHub-hosted jobs.
  The first runs the generated code with read-only repository permission; only
  after it passes does a separate job receive permission to push and open the
  PR. Generated code never executes with repository write access.
- **Isolate.** A dedicated user (above), and ideally a VM/container with limited
  outbound network, so a compromised run can't reach the rest of Mothership.

## 5. Repo configuration (one-time)

Set these as repo **Variables** (Settings → Secrets and variables → Actions →
Variables) — not secrets:

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_DAILY_CAP` | `10` | Runaway breaker: pause all loops if this many agent issues+PRs are created in 24h. |

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
2. **Review the candidate:** scheduled and default manual runs only create or
   update an advisory issue because catalogs can differ by account or CLI
   version.
3. **Confirmed PR-only fix:** after independent verification, run manually with
   `confirm_removals: true` and `dry_run: false`. The workflow opens a PR that
   always requires normal review and branch checks; it never auto-merges.

## What runs where

| Piece | Where | Notes |
|---|---|---|
| `scripts/agent/detect_drift.py` | runner | Deterministic; compares live discovery vs `DEFAULT_MODELS`. |
| `scripts/agent/preflight.py` | runner | Circuit breaker + kill switch (uses `gh`). |
| `scripts/agent/build_prompt.py` | runner | Turns the drift verdict into a bounded prompt. |
| `scripts/agent/apply_drift_fix.sh` | runner | **Tune this** to your local agent CLI/flags. |
| `scripts/agent/validate_patch.py` | runner | Rejects any patch outside models, its focused test, and the changelog. |
| Patch verification | GitHub-hosted runner | Applies the patch and runs the full suite with read-only repository permission. |
| Patch publication | GitHub-hosted runner | Reapplies the verified artifact, pushes, and opens the review-required PR without executing generated code. |
| `.github/workflows/agent-drift-watchdog.yml` | orchestration | Trusted triggers only; separates local agent execution from publication credentials. |
