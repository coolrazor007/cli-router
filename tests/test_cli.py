import sys
from pathlib import Path

import yaml

from cli_router.cli import main


def test_cli_help_displays_usage(capsys):
    exit_code = main(["--help"])

    assert exit_code == 0
    assert "cli-router" in capsys.readouterr().out


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
