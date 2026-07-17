import json
import sys
from pathlib import Path

import yaml

from cli_router.config import load_config
from cli_router.workflows import implement_workflow, plan_workflow, run_workflow


def write_config(tmp_path, planner_code, coder_code=None, stop_on_failure=True):
    coder_code = coder_code or "import sys; print('coded:' + sys.argv[1])"
    data = {
        "version": 1,
        "defaults": {
            "plan_file": "PLAN.md",
            "run_dir": ".cli-router/runs",
            "stop_on_failure": stop_on_failure,
        },
        "tools": {
            "planner": {
                "command": [sys.executable, "-c", planner_code, "{prompt}"],
                "output": {"format": "json", "extract": "result"},
            },
            "coder": {
                "command": [sys.executable, "-c", coder_code, "{prompt}"],
                "output": {"format": "text"},
            },
        },
        "workflows": {
            "default": {
                "stages": [
                    {
                        "id": "planner",
                        "tool": "planner",
                        "input_template": "plan {user_prompt}",
                        "output_file": "PLAN.md",
                    },
                    {
                        "id": "coder",
                        "tool": "coder",
                        "input_template": "implement {plan_path}: {user_prompt}",
                    },
                ]
            }
        },
    }
    Path(tmp_path, "cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_plan_workflow_writes_plan_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, "import json; print(json.dumps({'result': 'the plan'}))")

    summary = plan_workflow(load_config(), "add logging")

    assert summary.exit_code == 0
    assert Path("PLAN.md").read_text(encoding="utf-8") == "the plan"
    assert (summary.run_dir / "planner.stdout").exists()
    assert (summary.run_dir / "planner.extracted.md").read_text(encoding="utf-8") == "the plan"


def test_run_workflow_runs_planner_then_coder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, "import json; print(json.dumps({'result': 'the plan'}))")

    summary = run_workflow(load_config(), "add logging")

    assert summary.exit_code == 0
    assert [stage.stage_id for stage in summary.stages] == ["planner", "coder"]
    assert "coded:implement PLAN.md: add logging" in summary.stages[1].result.stdout


def test_run_workflow_records_diagnostics_and_metrics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, "import json; print(json.dumps({'result': 'the plan'}))")
    config = load_config()
    config.defaults["log_dir"] = str(tmp_path / "home" / ".cli-router" / "logs")

    summary = run_workflow(config, "add logging")

    assert summary.started_at
    assert summary.finished_at
    assert summary.duration_seconds >= 0
    assert summary.stages[0].started_at
    assert summary.stages[0].attempt == 1
    assert summary.stages[0].duration_seconds >= summary.stages[0].result.duration_seconds

    manifest = yaml.safe_load((summary.run_dir / "run.yaml").read_text(encoding="utf-8"))
    assert manifest["started_at"] == summary.started_at
    assert manifest["finished_at"] == summary.finished_at
    assert manifest["duration_seconds"] == summary.duration_seconds
    assert manifest["metrics"]["run_id"] == summary.run_dir.name
    assert manifest["metrics"]["workflow"] == "default"
    assert manifest["metrics"]["stage_count"] == 2
    assert manifest["metrics"]["retry_count"] == 0
    assert manifest["metrics"]["stages"][0]["stage_id"] == "planner"
    assert manifest["metrics"]["stages"][0]["duration_seconds"] >= 0
    assert manifest["metrics"]["stages"][0]["subprocess_seconds"] >= 0
    assert manifest["metrics"]["stages"][0]["stdout_bytes"] > 0

    log_dir = tmp_path / "home" / ".cli-router" / "logs"
    log_text = (log_dir / "cli-router.log").read_text(encoding="utf-8")
    assert "workflow_started" in log_text
    assert "stage_finished" in log_text
    metric_lines = (log_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(metric_lines[-1])["run_id"] == summary.run_dir.name


def test_run_workflow_forwards_previous_and_all_stage_outputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    echo = "import sys; print(sys.argv[1])"
    data = {
        "version": 1,
        "defaults": {"plan_file": "PLAN.md", "run_dir": ".cli-router/runs"},
        "tools": {
            "planner": {
                "command": [sys.executable, "-c", "import json; print(json.dumps({'result': 'THE PLAN'}))", "{prompt}"],
                "output": {"format": "json", "extract": "result"},
            },
            "echo": {"command": [sys.executable, "-c", echo, "{prompt}"], "output": {"format": "text"}},
        },
        "workflows": {
            "default": {
                "stages": [
                    {"id": "planner", "tool": "planner", "input_template": "plan {user_prompt}", "output_file": "PLAN.md"},
                    {"id": "coder", "tool": "echo", "input_template": "CODED"},
                    {"id": "qa", "tool": "echo", "input_template": "prev=<{previous_output}> all=<{all_stage_outputs}>"},
                ]
            }
        },
    }
    Path(tmp_path, "cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    summary = run_workflow(load_config(), "add logging")

    assert summary.exit_code == 0
    qa_prompt = summary.stages[2].result.stdout
    # previous_output is the immediately preceding stage's extracted output (coder), not the plan.
    assert "prev=<CODED" in qa_prompt
    assert "all=<" in qa_prompt
    # all_stage_outputs carries every completed stage so far, labeled by stage id.
    assert "## planner\nTHE PLAN" in qa_prompt
    assert "## coder\nCODED" in qa_prompt


def test_run_workflow_first_stage_has_empty_stage_output_variables(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    echo = "import sys; print(sys.argv[1])"
    data = {
        "version": 1,
        "defaults": {"plan_file": "PLAN.md", "run_dir": ".cli-router/runs"},
        "tools": {"echo": {"command": [sys.executable, "-c", echo, "{prompt}"], "output": {"format": "text"}}},
        "workflows": {
            "default": {
                "stages": [
                    {"id": "planner", "tool": "echo", "input_template": "prev=<{previous_output}> all=<{all_stage_outputs}>", "output_file": "PLAN.md"},
                ]
            }
        },
    }
    Path(tmp_path, "cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    summary = run_workflow(load_config(), "add logging")

    assert summary.exit_code == 0
    assert "prev=<> all=<>" in summary.stages[0].result.stdout


def test_run_manifest_omits_duplicated_stream_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(
        tmp_path,
        "import json; print(json.dumps({'result': 'the plan'}))",
        coder_code="import sys; sys.stderr.write('X' * 5000); print('done')",
    )

    summary = run_workflow(load_config(), "add logging")

    raw = (summary.run_dir / "run.yaml").read_text(encoding="utf-8")
    # The 5 KB of stderr lives only in the sidecar file, not inline in the manifest.
    assert "X" * 5000 not in raw
    assert (summary.run_dir / "coder.stderr").read_text(encoding="utf-8").count("X") == 5000

    manifest = yaml.safe_load(raw)
    coder = manifest["stages"][1]
    assert coder["stage_id"] == "coder"
    assert coder["exit_code"] == 0
    assert coder["stderr_bytes"] == 5000
    assert coder["artifacts"] == {
        "stdout": "coder.stdout",
        "stderr": "coder.stderr",
        "extracted": "coder.extracted.md",
    }
    # The exact prompt sent is still recorded for diagnostics.
    assert "command" in coder
    # No inline stdout/stderr streams remain on the stage record.
    assert "stdout" not in coder
    assert "stderr" not in coder
    assert "result" not in coder


def test_run_workflow_does_not_expand_placeholder_tokens_in_user_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, "import json; print(json.dumps({'result': 'the plan'}))")

    summary = run_workflow(load_config(), "add {plan_path} support")

    assert summary.exit_code == 0
    assert "coded:implement PLAN.md: add {plan_path} support" in summary.stages[1].result.stdout


def test_run_workflow_runs_enabled_stages_in_configured_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = {
        "version": 1,
        "defaults": {
            "plan_file": "PLAN.md",
            "run_dir": ".cli-router/runs",
            "stop_on_failure": True,
        },
        "tools": {
            "planner": {
                "command": [sys.executable, "-c", "import json; print(json.dumps({'result': 'the plan'}))"],
                "output": {"format": "json", "extract": "result"},
            },
            "reviewer": {
                "command": [sys.executable, "-c", "import sys; print('review:' + sys.argv[1])", "{prompt}"],
                "output": {"format": "text"},
            },
            "disabled-check": {
                "command": [sys.executable, "-c", "print('should not run')"],
                "output": {"format": "text"},
            },
            "coder": {
                "command": [sys.executable, "-c", "import sys; print('coded:' + sys.argv[1])", "{prompt}"],
                "output": {"format": "text"},
            },
        },
        "workflows": {
            "default": {
                "stages": [
                    {"id": "planner", "tool": "planner", "input_template": "plan {user_prompt}", "output_file": "PLAN.md"},
                    {"id": "review", "tool": "reviewer", "input_template": "review {plan_path}: {user_prompt}"},
                    {"id": "disabled-check", "tool": "disabled-check", "enabled": False},
                    {"id": "coder", "tool": "coder", "input_template": "code {plan_path}: {user_prompt}"},
                ]
            }
        },
    }
    Path("cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    summary = run_workflow(load_config(), "add logging")

    assert summary.exit_code == 0
    assert [stage.stage_id for stage in summary.stages] == ["planner", "review", "coder"]
    assert (summary.run_dir / "review.stdout").read_text(encoding="utf-8") == "review:review PLAN.md: add logging\n"
    assert not (summary.run_dir / "disabled-check.stdout").exists()


def test_run_workflow_can_select_and_order_stages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = {
        "version": 1,
        "defaults": {
            "plan_file": "PLAN.md",
            "run_dir": ".cli-router/runs",
            "stop_on_failure": True,
        },
        "tools": {
            "alpha": {"command": [sys.executable, "-c", "print('alpha')"], "output": {"format": "text"}},
            "beta": {"command": [sys.executable, "-c", "print('beta')"], "output": {"format": "text"}},
            "gamma": {"command": [sys.executable, "-c", "print('gamma')"], "output": {"format": "text"}},
        },
        "workflows": {
            "default": {
                "stages": [
                    {"id": "alpha", "tool": "alpha"},
                    {"id": "beta", "tool": "beta", "enabled": False},
                    {"id": "gamma", "tool": "gamma"},
                ]
            }
        },
    }
    Path("cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    summary = run_workflow(load_config(), "ignored", "default", stage_names=["gamma", "beta"])

    assert summary.exit_code == 0
    assert [stage.stage_id for stage in summary.stages] == ["gamma", "beta"]
    assert (summary.run_dir / "gamma.stdout").read_text(encoding="utf-8") == "gamma\n"
    assert (summary.run_dir / "beta.stdout").read_text(encoding="utf-8") == "beta\n"
    assert not (summary.run_dir / "alpha.stdout").exists()


def test_run_workflow_supports_duplicate_stage_templates_with_unique_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = {
        "version": 1,
        "defaults": {
            "plan_file": "PLAN.md",
            "run_dir": ".cli-router/runs",
            "stop_on_failure": True,
        },
        "tools": {
            "planner": {
                "command": [sys.executable, "-c", "import json; print(json.dumps({'result': 'plan'}))"],
                "output": {"format": "json", "extract": "result"},
            },
            "echo": {
                "command": [sys.executable, "-c", "import sys; print(sys.argv[1])", "{prompt}"],
                "output": {"format": "text"},
            },
        },
        "workflows": {
            "default": {
                "stages": [
                    {"id": "planner", "tool": "planner", "input_template": "plan {user_prompt}", "output_file": "PLAN.md"},
                    {"id": "coder", "tool": "echo", "input_template": "code {plan_path}"},
                    {"id": "qa", "tool": "echo", "input_template": "qa {plan_path}"},
                    {"id": "coder-2", "tool": "echo", "input_template": "code again {plan_path}"},
                    {"id": "summary", "tool": "echo", "input_template": "summary {user_prompt}"},
                ]
            }
        },
    }
    Path("cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    summary = run_workflow(load_config(), "duplicate coder")

    assert summary.exit_code == 0
    assert [stage.stage_id for stage in summary.stages] == ["planner", "coder", "qa", "coder-2", "summary"]
    assert (summary.run_dir / "coder.stdout").read_text(encoding="utf-8") == "code PLAN.md\n"
    assert (summary.run_dir / "coder-2.stdout").read_text(encoding="utf-8") == "code again PLAN.md\n"


def test_implement_workflow_fails_when_plan_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, "import json; print(json.dumps({'result': 'the plan'}))")

    summary = implement_workflow(load_config(), "default")

    assert summary.exit_code != 0
    assert "PLAN.md" in summary.error


def test_run_workflow_stops_before_coder_on_planner_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, "import sys; print('bad'); sys.exit(5)")

    summary = run_workflow(load_config(), "add logging")

    assert summary.exit_code == 5
    assert [stage.stage_id for stage in summary.stages] == ["planner"]


def test_run_workflow_keeps_first_failure_when_configured_to_continue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(
        tmp_path,
        "import sys; print('bad'); sys.exit(5)",
        coder_code="print('later stage succeeded')",
        stop_on_failure=False,
    )

    summary = run_workflow(load_config(), "add logging")

    assert [stage.result.returncode for stage in summary.stages] == [5, 0]
    assert summary.exit_code == 5
    assert summary.error is not None
    assert "planner" in summary.error


def test_run_workflow_fails_when_no_stages_are_enabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, "import json; print(json.dumps({'result': 'the plan'}))")
    data = yaml.safe_load(Path("cli-router.yaml").read_text(encoding="utf-8"))
    for stage in data["workflows"]["default"]["stages"]:
        stage["enabled"] = False
    Path("cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    summary = run_workflow(load_config(), "add logging")

    assert summary.exit_code == 2
    assert summary.error == "Workflow has no enabled stages"
    assert summary.stages == []
    manifest = yaml.safe_load((summary.run_dir / "run.yaml").read_text(encoding="utf-8"))
    assert manifest["exit_code"] == 2
    assert manifest["error"] == summary.error


def test_implement_workflow_fails_when_no_post_planner_stages_are_enabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, "import json; print(json.dumps({'result': 'the plan'}))")
    data = yaml.safe_load(Path("cli-router.yaml").read_text(encoding="utf-8"))
    data["workflows"]["default"]["stages"][1]["enabled"] = False
    Path("cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    Path("PLAN.md").write_text("the plan", encoding="utf-8")

    summary = implement_workflow(load_config(), "default")

    assert summary.exit_code == 2
    assert summary.error == "Workflow has no enabled post-planner stages"
    assert summary.stages == []
    manifest = yaml.safe_load((summary.run_dir / "run.yaml").read_text(encoding="utf-8"))
    assert manifest["exit_code"] == 2
    assert manifest["error"] == summary.error


def test_run_workflow_surfaces_auth_required_provider_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, "import sys; print('Not logged in · Please run /login'); sys.exit(1)")

    summary = run_workflow(load_config(), "add logging")

    assert summary.exit_code == 1
    assert summary.stages[0].failure_kind == "auth_required"
    assert "authentication" in summary.error.lower()
    assert "Not logged in" in summary.error


def test_run_workflow_uses_stage_fallback_tool_after_usage_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = {
        "version": 1,
        "defaults": {
            "plan_file": "PLAN.md",
            "run_dir": ".cli-router/runs",
            "stop_on_failure": True,
        },
        "tools": {
            "claude-planner": {
                "command": [
                    sys.executable,
                    "-c",
                    "import sys; print('Claude usage limit reached', file=sys.stderr); sys.exit(1)",
                ],
                "output": {"format": "json", "extract": "result"},
            },
            "codex-planner": {
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'result': 'fallback plan'}))",
                ],
                "output": {"format": "json", "extract": "result"},
            },
            "coder": {
                "command": [sys.executable, "-c", "import sys; print('coded:' + sys.argv[1])", "{prompt}"],
                "output": {"format": "text"},
            },
        },
        "workflows": {
            "default": {
                "stages": [
                    {
                        "id": "planner",
                        "tool": "claude-planner",
                        "fallback_tools": ["codex-planner"],
                        "input_template": "plan {user_prompt}",
                        "output_file": "PLAN.md",
                    },
                    {
                        "id": "coder",
                        "tool": "coder",
                        "input_template": "implement {plan_path}: {user_prompt}",
                    },
                ]
            }
        },
    }
    Path("cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    summary = run_workflow(load_config(), "add logging")

    assert summary.exit_code == 0
    assert Path("PLAN.md").read_text(encoding="utf-8") == "fallback plan"
    assert [(stage.stage_id, stage.tool, stage.failure_kind) for stage in summary.stages] == [
        ("planner", "claude-planner", "usage_limit"),
        ("planner", "codex-planner", None),
        ("coder", "coder", None),
    ]
    assert (summary.run_dir / "planner.stderr").read_text(encoding="utf-8") == "Claude usage limit reached\n"
    assert (summary.run_dir / "planner.codex-planner.extracted.md").read_text(encoding="utf-8") == "fallback plan"


def test_run_workflow_observer_receives_stage_events_in_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, "import json; print(json.dumps({'result': 'the plan'}), flush=True)")
    events = []

    class RecordingObserver:
        def stage_started(self, stage_id, tool, attempt):
            events.append(("started", stage_id, tool, attempt))

        def stage_output(self, stage_id, tool, line):
            events.append(("output", stage_id, tool, line))

        def stage_error(self, stage_id, tool, line):
            events.append(("error", stage_id, tool, line))

        def stage_finished(self, stage):
            events.append(("finished", stage.stage_id, stage.tool, stage.result.returncode))

    summary = run_workflow(load_config(), "add logging", observer=RecordingObserver())

    assert summary.exit_code == 0
    assert events[0] == ("started", "planner", "planner", 1)
    assert events[1][0:3] == ("output", "planner", "planner")
    assert events[2] == ("finished", "planner", "planner", 0)
    assert events[3] == ("started", "coder", "coder", 1)
    assert events[-1] == ("finished", "coder", "coder", 0)
