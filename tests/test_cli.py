import json
import sys
from pathlib import Path

import yaml

import cli_router.cli as cli
from cli_router.cli import main


def test_cli_help_displays_usage(capsys):
    exit_code = main(["--help"])

    assert exit_code == 0
    assert "cli-router" in capsys.readouterr().out


def test_cli_without_command_opens_tui(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "tools": {"planner": {"command": [sys.executable, "-c", "print(1)"]}},
                "workflows": {"default": {"stages": [{"id": "planner", "tool": "planner"}]}},
            }
        ),
        encoding="utf-8",
    )
    called = {}

    def fake_run_tui(config, workflow_name="default", prompt=None):
        called["workflow_name"] = workflow_name
        called["prompt"] = prompt
        called["tools"] = sorted(config.tools)
        return 0

    monkeypatch.setattr(cli, "run_tui", fake_run_tui)

    exit_code = main([])

    assert exit_code == 0
    assert called == {"workflow_name": "default", "prompt": None, "tools": ["generic-coder", "generic-planner", "planner"]}


def test_cli_doctor_reports_health_and_fails_on_broken_provider(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from cli_router.doctor import ProviderHealth

    health = [
        ProviderHealth("codex", True, True, ["gpt-5.6-sol"], "catalog", "2 models"),
        ProviderHealth("grok", True, False, [], "none", "did not parse", raw_output="junk", command=["grok", "models"]),
    ]
    monkeypatch.setattr(cli, "diagnose", lambda **kwargs: health)

    exit_code = main(["doctor"])
    out = capsys.readouterr().out

    assert "codex" in out and "ok" in out
    assert "grok" in out and "drift" in out
    assert "--repair" in out  # nudges the user toward repair
    assert exit_code == 1  # a present provider has no models


def test_cli_doctor_repair_recovers_and_succeeds(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from cli_router.doctor import ProviderHealth, RepairResult

    drifting = [
        ProviderHealth("codex", True, True, ["gpt-5.6-sol"], "catalog", "ok"),
        ProviderHealth("grok", True, False, [], "none", "drift", raw_output="junk", command=["grok", "models"]),
    ]
    recovered = [
        ProviderHealth("codex", True, True, ["gpt-5.6-sol"], "catalog", "ok"),
        ProviderHealth("grok", True, False, ["grok-4.5"], "cache", "using 1 cached"),
    ]
    states = iter([drifting, recovered])
    monkeypatch.setattr(cli, "diagnose", lambda **kwargs: next(states))
    monkeypatch.setattr(
        cli, "repair", lambda health, **kwargs: [RepairResult("grok", True, ["grok-4.5"], "recovered 1 models via codex", "codex")]
    )

    exit_code = main(["doctor", "--repair"])
    out = capsys.readouterr().out

    assert "doctor fixed: grok" in out
    assert exit_code == 0  # grok now has a (cached) model list


def test_cli_config_show_prints_loaded_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "defaults": {"plan_file": "PLAN.md"},
                "tools": {"planner": {"command": [sys.executable, "-c", "print(1)"]}},
                "workflows": {"default": {"stages": []}},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["config", "show"])

    assert exit_code == 0
    assert "PLAN.md" in capsys.readouterr().out


def test_cli_tools_list_prints_tool_names(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "tools": {"planner": {"command": [sys.executable, "-c", "print(1)"]}},
                "workflows": {"default": {"stages": []}},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["tools", "list"])

    assert exit_code == 0
    assert "planner" in capsys.readouterr().out


def test_cli_tools_test_writes_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "defaults": {"run_dir": ".cli-router/runs"},
                "tools": {
                    "planner": {
                        "command": [sys.executable, "-c", "import sys; print(sys.argv[1])", "{prompt}"]
                    }
                },
                "workflows": {"default": {"stages": []}},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["tools", "test", "planner"])

    assert exit_code == 0
    run_dirs = list(Path(".cli-router/runs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "planner.stdout").read_text(encoding="utf-8") == "CLI-Router tool test\n"


def test_cli_runs_list_and_show_inspect_existing_artifacts(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "defaults": {"run_dir": ".cli-router/runs"},
                "tools": {"planner": {"command": [sys.executable, "-c", "print('plan output')"]}},
                "workflows": {"default": {"stages": [{"id": "planner", "tool": "planner"}]}},
            }
        ),
        encoding="utf-8",
    )

    assert main(["run", "remember this prompt"]) == 0
    capsys.readouterr()
    run_id = next(Path(".cli-router/runs").iterdir()).name

    assert main(["runs", "list"]) == 0
    list_output = capsys.readouterr().out
    assert run_id in list_output
    assert "default" in list_output
    assert "remember this prompt" in list_output

    assert main(["runs", "show", run_id[:13]]) == 0
    show_output = capsys.readouterr().out
    assert f"run: {run_id}" in show_output
    assert "workflow: default" in show_output
    assert "duration_seconds:" in show_output
    assert "planner: tool planner, exit 0, failure none, duration" in show_output
    assert "planner.stdout" in show_output


def test_cli_run_accepts_explicit_stage_selection(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "defaults": {"run_dir": ".cli-router/runs"},
                "tools": {
                    "alpha": {"command": [sys.executable, "-c", "print('alpha')"]},
                    "beta": {"command": [sys.executable, "-c", "print('beta')"], "output": {"format": "text"}},
                },
                "workflows": {
                    "default": {
                        "stages": [
                            {"id": "alpha", "tool": "alpha"},
                            {"id": "beta", "tool": "beta", "enabled": False},
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["run", "ignored", "--stages", "beta"])

    assert exit_code == 0
    assert "beta (beta): exit 0" in capsys.readouterr().out
    run_dirs = list(Path(".cli-router/runs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "beta.stdout").read_text(encoding="utf-8") == "beta\n"
    assert not (run_dirs[0] / "alpha.stdout").exists()


def test_cli_version_json_is_machine_readable_without_loading_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text("not: [valid", encoding="utf-8")

    exit_code = main(["--version", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {
        "schema_version": 1,
        "cli_router_version": "0.3.1",
        "command": "version",
        "config": {"source": None, "checksum": None},
        "workflow": None,
        "run_id": None,
        "run_dir": None,
        "overall_outcome": "success",
        "exit_code": 0,
        "duration_seconds": 0.0,
        "fallback_used": False,
        "fallback_reason": None,
        "artifacts": {},
        "stages": [],
        "error": None,
    }


def test_cli_check_json_includes_config_identity(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "cli-router.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "requires_cli_router": ">=0.3.1,<0.4.0",
                "tools": {"reviewer": {"command": ["reviewer"]}},
                "workflows": {"default": {"stages": [{"id": "review", "tool": "reviewer"}]}},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["check", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["command"] == "check"
    assert payload["config"]["source"] == str(config_path)
    assert payload["config"]["checksum"].startswith("sha256:")
    assert len(payload["config"]["checksum"]) == 71
    assert payload["overall_outcome"] == "success"


def test_cli_run_json_reports_attempts_models_fallback_and_artifacts(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    data = {
        "version": 1,
        "defaults": {"run_dir": ".cli-router/runs"},
        "tools": {
            "grok-reviewer": {
                "provider": "grok",
                "model": "grok-review",
                "command": [
                    sys.executable,
                    "-c",
                    "import sys; print('Not logged in', file=sys.stderr); sys.exit(1)",
                ],
                "output": {"format": "text"},
            },
            "fable-reviewer": {
                "provider": "claude",
                "model": "claude-fable-5",
                "command": [sys.executable, "-c", "print('PASS')"],
                "output": {"format": "text"},
            },
        },
        "workflows": {
            "default": {
                "stages": [
                    {
                        "id": "review",
                        "tool": "grok-reviewer",
                        "fallback_tools": [{"tool": "fable-reviewer", "on": ["auth_required"]}],
                        "max_fallback_attempts": 1,
                    }
                ]
            }
        },
    }
    Path("cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    exit_code = main(["run", "review target", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["command"] == "run"
    assert payload["workflow"] == "default"
    assert payload["run_id"] == Path(payload["run_dir"]).name
    assert payload["overall_outcome"] == "success"
    assert payload["fallback_used"] is True
    assert payload["fallback_reason"] == "allowed_failure_kind"
    assert payload["artifacts"]["run_manifest"].endswith("/run.yaml")
    assert payload["stages"][0] | {
        "stage_id": "review",
        "tool": "grok-reviewer",
        "provider": "grok",
        "model": "grok-review",
        "attempt": 1,
        "exit_code": 1,
        "failure_kind": "auth_required",
        "fallback_used": False,
        "fallback_reason": None,
    } == payload["stages"][0]
    assert payload["stages"][1]["tool"] == "fable-reviewer"
    assert payload["stages"][1]["model"] == "claude-fable-5"
    assert payload["stages"][1]["attempt"] == 2
    assert payload["stages"][1]["fallback_used"] is True
    assert payload["stages"][1]["primary_failure_kind"] == "auth_required"
    assert Path(payload["stages"][1]["artifacts"]["stdout"]).read_text(encoding="utf-8") == "PASS\n"


def test_cli_tools_test_json_reports_receipt(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "defaults": {"run_dir": ".cli-router/runs"},
                "tools": {
                    "reviewer": {
                        "provider": "grok",
                        "model": "grok-review",
                        "command": [sys.executable, "-c", "print('ok')"],
                    }
                },
                "workflows": {"default": {"stages": []}},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["tools", "test", "reviewer", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["command"] == "tools test"
    assert payload["run_id"]
    assert payload["overall_outcome"] == "success"
    assert payload["stages"][0]["tool"] == "reviewer"
    assert payload["stages"][0]["model"] == "grok-review"
    assert payload["stages"][0]["attempt"] == 1
    assert payload["stages"][0]["failure_kind"] is None
    assert Path(payload["stages"][0]["artifacts"]["stdout"]).exists()


def test_cli_implement_json_reports_failed_outcome(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("PLAN.md").write_text("plan", encoding="utf-8")
    Path("cli-router.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "defaults": {"plan_file": "PLAN.md", "run_dir": ".cli-router/runs"},
                "tools": {
                    "planner": {"command": [sys.executable, "-c", "print('plan')"]},
                    "coder": {
                        "provider": "codex",
                        "model": "coder-model",
                        "command": [sys.executable, "-c", "import sys; sys.exit(7)"],
                    },
                },
                "workflows": {
                    "default": {
                        "stages": [
                            {"id": "planner", "tool": "planner"},
                            {"id": "coder", "tool": "coder"},
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["implement", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 7
    assert payload["command"] == "implement"
    assert payload["overall_outcome"] == "failed"
    assert payload["stages"][0]["stage_id"] == "coder"
    assert payload["stages"][0]["exit_code"] == 7
    assert payload["stages"][0]["failure_kind"] == "command_failed"


def test_cli_check_json_reports_config_error_as_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        'version: 1\nrequires_cli_router: ">=99"\n',
        encoding="utf-8",
    )

    exit_code = main(["check", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 2
    assert captured.err == ""
    assert payload["command"] == "check"
    assert payload["overall_outcome"] == "failed"
    assert payload["exit_code"] == 2
    assert "requires cli-router >=99" in payload["error"]
