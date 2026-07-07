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
