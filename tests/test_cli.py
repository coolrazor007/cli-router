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
    assert "beta: exit 0" in capsys.readouterr().out
    run_dirs = list(Path(".cli-router/runs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "beta.stdout").read_text(encoding="utf-8") == "beta\n"
    assert not (run_dirs[0] / "alpha.stdout").exists()
