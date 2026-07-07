from pathlib import Path

import pytest

from cli_router.config import ConfigError, load_config


def test_load_config_uses_local_cli_router_yaml_first(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        """
version: 1
defaults:
  plan_file: CUSTOM.md
tools:
  planner:
    command: ["echo", "{prompt}"]
workflows:
  default:
    stages:
      - id: planner
        tool: planner
        input_template: "{user_prompt}"
        output_file: CUSTOM.md
""",
        encoding="utf-8",
    )

    config = load_config()

    assert config.defaults["plan_file"] == "CUSTOM.md"
    assert config.source == tmp_path / "cli-router.yaml"


def test_load_config_falls_back_to_built_in_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.defaults["plan_file"] == "PLAN.md"
    assert "default" in config.workflows


def test_invalid_config_version_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text("version: 2\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config()
