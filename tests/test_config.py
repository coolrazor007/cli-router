from pathlib import Path

import pytest

from cli_router.config import ConfigError, RouterConfig, load_config, save_config, user_config_path


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


def test_load_config_uses_home_cli_router_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".cli-router" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        """
version: 1
defaults:
  plan_file: HOME_PLAN.md
tools:
  planner:
    command: ["echo", "{prompt}"]
workflows:
  default:
    stages:
      - id: planner
        tool: planner
""",
        encoding="utf-8",
    )

    config = load_config()

    assert config.source == config_path
    assert config.defaults["plan_file"] == "HOME_PLAN.md"


def test_save_config_writes_home_cli_router_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = RouterConfig(
        {
            "version": 1,
            "tools": {"planner": {"command": ["echo", "{prompt}"]}},
            "workflows": {"default": {"stages": [{"id": "planner", "tool": "planner"}]}},
        },
        None,
    )

    saved_path = save_config(config)

    assert saved_path == user_config_path()
    assert "planner" in saved_path.read_text(encoding="utf-8")


def test_invalid_config_version_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text("version: 2\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config()


def test_duplicate_workflow_stage_ids_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        """
version: 1
tools:
  first:
    command: ["echo", "first"]
  second:
    command: ["echo", "second"]
workflows:
  default:
    stages:
      - id: review
        tool: first
      - id: review
        tool: second
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="duplicate stage id"):
        load_config()


def test_stage_library_validates_template_tools(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        """
version: 1
tools:
  planner:
    command: ["echo", "plan"]
stage_library:
  - id: reviewer
    tool: missing
    input_template: "Review {user_prompt}"
workflows:
  default:
    stages:
      - id: planner
        tool: planner
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="stage_library references unknown tool 'missing'"):
        load_config()
