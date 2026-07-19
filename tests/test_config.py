from pathlib import Path

import pytest
import yaml

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


def test_legacy_config_without_version_remains_version_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        """
tools:
  reviewer:
    command: ["reviewer"]
workflows:
  default:
    stages:
      - id: review
        tool: reviewer
""",
        encoding="utf-8",
    )

    config = load_config()

    assert config.data["version"] == 1
    assert "requires_cli_router" not in config.data


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
    Path("cli-router.yaml").write_text("version: 3\n", encoding="utf-8")

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


def test_structured_fallback_policy_is_valid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        """
version: 1
tools:
  primary:
    command: ["primary"]
  backup:
    command: ["backup"]
workflows:
  default:
    stages:
      - id: review
        tool: primary
        fallback_tools:
          - tool: backup
            on: [auth_required, usage_limit, timeout, transport_failure]
        max_fallback_attempts: 1
""",
        encoding="utf-8",
    )

    config = load_config()

    fallback = config.workflows["default"]["stages"][0]["fallback_tools"][0]
    assert fallback == {
        "tool": "backup",
        "on": ["auth_required", "usage_limit", "timeout", "transport_failure"],
    }


@pytest.mark.parametrize(
    "failure_kind",
    [
        "command_failed",
        "extraction_failed",
        "unsupported_model",
        "configuration_error",
        "target_mutation",
    ],
)
def test_fallback_policy_rejects_unsafe_failure_kinds(tmp_path, monkeypatch, failure_kind):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        f"""
version: 1
tools:
  primary:
    command: ["primary"]
  backup:
    command: ["backup"]
workflows:
  default:
    stages:
      - id: review
        tool: primary
        fallback_tools:
          - tool: backup
            on: [{failure_kind}]
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="not safe for fallback"):
        load_config()


@pytest.mark.parametrize("value", [-1, 1.5, "one", True])
def test_max_fallback_attempts_must_be_a_nonnegative_integer(tmp_path, monkeypatch, value):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        f"""
version: 1
tools:
  primary:
    command: ["primary"]
  backup:
    command: ["backup"]
workflows:
  default:
    stages:
      - id: review
        tool: primary
        fallback_tools: [backup]
        max_fallback_attempts: {value!r}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="max_fallback_attempts"):
        load_config()


@pytest.mark.parametrize(
    ("tool_setting", "message"),
    [
        ({"cwd": ["not", "a", "path"]}, "cwd"),
        ({"environment_mode": "everything"}, "environment_mode"),
        ({"environment_allowlist": "PATH"}, "environment_allowlist"),
        ({"environment": ["TERM=dumb"]}, "environment must be a mapping"),
        ({"environment": {"COUNT": 1}}, "environment values"),
        ({"environment_unset": "TOKEN"}, "environment_unset"),
        ({"stdin": "pipe"}, "stdin"),
        ({"redact_environment_values": "TOKEN"}, "redact_environment_values"),
    ],
)
def test_tool_execution_policy_is_validated(tmp_path, monkeypatch, tool_setting, message):
    monkeypatch.chdir(tmp_path)
    data = {
        "version": 1,
        "tools": {"reviewer": {"command": ["reviewer"], **tool_setting}},
        "workflows": {"default": {"stages": [{"id": "review", "tool": "reviewer"}]}},
    }
    Path("cli-router.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config()


def test_requires_cli_router_accepts_running_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        """
version: 1
requires_cli_router: ">=0.3.2,<0.4.0"
tools:
  reviewer:
    command: ["reviewer"]
workflows:
  default:
    stages:
      - id: review
        tool: reviewer
""",
        encoding="utf-8",
    )

    config = load_config()

    assert config.data["requires_cli_router"] == ">=0.3.2,<0.4.0"


def test_config_version_two_requires_and_accepts_compatible_router_range(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        """
version: 2
requires_cli_router: ">=0.3.2,<0.4.0"
tools:
  reviewer:
    command: ["reviewer"]
workflows:
  default:
    stages:
      - id: review
        tool: reviewer
""",
        encoding="utf-8",
    )

    config = load_config()

    assert config.data["version"] == 2


def test_config_version_two_requires_compatibility_declaration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text("version: 2\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="version 2.*requires_cli_router"):
        load_config()


def test_requires_cli_router_rejects_incompatible_running_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        'version: 1\nrequires_cli_router: ">=99"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"requires cli-router >=99.*running 0\.3\.2"):
        load_config()


@pytest.mark.parametrize("requirement", [">=not-a-version", 3.1])
def test_requires_cli_router_must_be_a_valid_specifier(tmp_path, monkeypatch, requirement):
    monkeypatch.chdir(tmp_path)
    Path("cli-router.yaml").write_text(
        yaml.safe_dump({"version": 1, "requires_cli_router": requirement}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="requires_cli_router"):
        load_config()
