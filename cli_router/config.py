"""Configuration loading and validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from . import __version__
from .failures import FALLBACK_SAFE_FAILURE_KINDS

CURRENT_CONFIG_VERSION = 2
CURRENT_CLI_ROUTER_REQUIREMENT = ">=0.3.2,<0.4.0"
SUPPORTED_CONFIG_VERSIONS = frozenset({1, CURRENT_CONFIG_VERSION})


class ConfigError(RuntimeError):
    """Raised when CLI-Router configuration is invalid."""


@dataclass(frozen=True)
class RouterConfig:
    data: dict[str, Any]
    source: Path | None
    source_checksum: str | None = None
    effective_checksum: str | None = None

    @property
    def defaults(self) -> dict[str, Any]:
        return self.data.setdefault("defaults", {})

    @property
    def tools(self) -> dict[str, Any]:
        return self.data.setdefault("tools", {})

    @property
    def workflows(self) -> dict[str, Any]:
        return self.data.setdefault("workflows", {})

    @property
    def stage_library(self) -> list[Any]:
        return self.data.setdefault("stage_library", [])


def user_config_path() -> Path:
    return Path.home() / ".cli-router" / "config.yaml"


def config_candidates() -> tuple[Path, ...]:
    return (
        Path("cli-router.yaml"),
        Path(".cli-router.yaml"),
        user_config_path(),
        Path.home() / ".config" / "cli-router" / "config.yaml",
    )


def load_config(path: str | Path | None = None) -> RouterConfig:
    source = Path(path).resolve() if path else _find_config()
    config, built_in_bytes = _built_in_config()
    source_bytes = built_in_bytes

    if source:
        user_config, source_bytes = _read_yaml(source)
        _validate_version(user_config, source)
        config = _deep_merge(config, user_config)
        config["version"] = user_config.get("version", 1)
        if "requires_cli_router" not in user_config:
            config.pop("requires_cli_router", None)

    _validate_config(config, source)
    return RouterConfig(
        config,
        source,
        source_checksum=_checksum_bytes(source_bytes),
        effective_checksum=_checksum_effective_config(config),
    )


def config_to_yaml(config: RouterConfig) -> str:
    return yaml.safe_dump(config.data, sort_keys=False)


def config_checksum(config: RouterConfig) -> str | None:
    return config.source_checksum


def config_source_identity(config: RouterConfig) -> str:
    return str(config.source) if config.source is not None else "built-in"


def config_identity(config: RouterConfig) -> dict[str, str | None]:
    return {
        "source": config_source_identity(config),
        "checksum": config.source_checksum,
        "effective_checksum": config.effective_checksum,
    }


def save_config(config: RouterConfig, path: str | Path | None = None) -> Path:
    target = Path(path) if path else user_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(config_to_yaml(config), encoding="utf-8")
    return target


def _find_config() -> Path | None:
    for candidate in config_candidates():
        if candidate.exists():
            return candidate.resolve()
    return None


def _built_in_config() -> tuple[dict[str, Any], bytes]:
    content = resources.files("cli_router.presets").joinpath("generic.yaml").read_bytes()
    data = yaml.safe_load(content.decode("utf-8")) or {}
    _validate_version(data, None)
    return data, content


def _read_yaml(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        content = path.read_bytes()
        loaded = yaml.safe_load(content.decode("utf-8"))
    except OSError as exc:
        raise ConfigError(f"Could not read config {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Config {path} must be UTF-8: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if loaded is None:
        return {}, content
    if not isinstance(loaded, dict):
        raise ConfigError(f"Config {path} must contain a YAML mapping")
    return loaded, content


def _checksum_bytes(content: bytes) -> str:
    return f"sha256:{sha256(content).hexdigest()}"


def _checksum_effective_config(data: dict[str, Any]) -> str:
    canonical = yaml.safe_dump(data, sort_keys=True, allow_unicode=True).encode("utf-8")
    return _checksum_bytes(canonical)


def _validate_version(data: dict[str, Any], source: Path | None) -> None:
    version = data.get("version", 1)
    if version not in SUPPORTED_CONFIG_VERSIONS:
        location = f" in {source}" if source else ""
        raise ConfigError(f"Unsupported config version{location}: {version}")


def _validate_config(data: dict[str, Any], source: Path | None) -> None:
    _validate_version(data, source)
    _validate_cli_router_requirement(data, source)
    if not isinstance(data.get("defaults", {}), dict):
        raise ConfigError("defaults must be a mapping")
    if not isinstance(data.get("tools", {}), dict):
        raise ConfigError("tools must be a mapping")
    if not isinstance(data.get("workflows", {}), dict):
        raise ConfigError("workflows must be a mapping")

    for name, tool in data.get("tools", {}).items():
        if not isinstance(tool, dict):
            raise ConfigError(f"tool {name!r} must be a mapping")
        if "command" not in tool:
            raise ConfigError(f"tool {name!r} is missing command")
        _validate_tool_execution_policy(name, tool)

    stage_library = data.get("stage_library", [])
    if not isinstance(stage_library, list):
        raise ConfigError("stage_library must be a list")
    for template in stage_library:
        if not isinstance(template, dict):
            raise ConfigError("stage_library template must be a mapping")
        if "id" not in template or "tool" not in template or "input_template" not in template:
            raise ConfigError("stage_library template is missing id, tool, or input_template")
        if template["tool"] not in data.get("tools", {}):
            raise ConfigError(f"stage_library references unknown tool {template['tool']!r}")

    for name, workflow in data.get("workflows", {}).items():
        if not isinstance(workflow, dict):
            raise ConfigError(f"workflow {name!r} must be a mapping")
        stages = workflow.get("stages", [])
        if not isinstance(stages, list):
            raise ConfigError(f"workflow {name!r} stages must be a list")
        stage_ids: set[str] = set()
        for stage in stages:
            if not isinstance(stage, dict):
                raise ConfigError(f"workflow {name!r} stage must be a mapping")
            if "id" not in stage or "tool" not in stage:
                raise ConfigError(f"workflow {name!r} stage is missing id or tool")
            stage_id = str(stage["id"])
            if stage_id in stage_ids:
                raise ConfigError(f"workflow {name!r} has duplicate stage id {stage_id!r}")
            stage_ids.add(stage_id)
            if stage["tool"] not in data.get("tools", {}):
                raise ConfigError(f"workflow {name!r} references unknown tool {stage['tool']!r}")
            fallback_tools = stage.get("fallback_tools", [])
            if not isinstance(fallback_tools, list):
                raise ConfigError(f"workflow {name!r} stage fallback_tools must be a list")
            for fallback in fallback_tools:
                fallback_tool = _validate_fallback_policy(name, fallback)
                if fallback_tool not in data.get("tools", {}):
                    raise ConfigError(f"workflow {name!r} references unknown fallback tool {fallback_tool!r}")
            max_attempts = stage.get("max_fallback_attempts", len(fallback_tools))
            if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 0:
                raise ConfigError(
                    f"workflow {name!r} stage max_fallback_attempts must be a nonnegative integer"
                )


def _validate_fallback_policy(workflow_name: str, fallback: Any) -> str:
    if isinstance(fallback, str) and fallback:
        return fallback
    if not isinstance(fallback, dict):
        raise ConfigError(
            f"workflow {workflow_name!r} fallback must be a tool name or a policy mapping"
        )
    # PyYAML follows YAML 1.1 and parses an unquoted ``on`` key as boolean true.
    # Normalize the documented spelling so configs can use ``on: [...]`` safely.
    if True in fallback and "on" not in fallback:
        fallback["on"] = fallback.pop(True)
    unknown_keys = set(fallback) - {"tool", "on"}
    if unknown_keys:
        unknown = ", ".join(sorted(str(key) for key in unknown_keys))
        raise ConfigError(f"workflow {workflow_name!r} fallback has unknown keys: {unknown}")
    tool = fallback.get("tool")
    if not isinstance(tool, str) or not tool:
        raise ConfigError(f"workflow {workflow_name!r} fallback tool must be a nonempty string")
    failure_kinds = fallback.get("on")
    if not isinstance(failure_kinds, list) or not failure_kinds:
        raise ConfigError(f"workflow {workflow_name!r} fallback on must be a nonempty list")
    for failure_kind in failure_kinds:
        if failure_kind not in FALLBACK_SAFE_FAILURE_KINDS:
            raise ConfigError(
                f"workflow {workflow_name!r} failure kind {failure_kind!r} is not safe for fallback"
            )
    return tool


def _validate_tool_execution_policy(name: str, tool: dict[str, Any]) -> None:
    cwd = tool.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd):
        raise ConfigError(f"tool {name!r} cwd must be a nonempty string")

    environment_mode = tool.get("environment_mode", "inherit")
    if environment_mode not in {"inherit", "allowlist"}:
        raise ConfigError(f"tool {name!r} environment_mode must be 'inherit' or 'allowlist'")

    _validate_string_list(name, tool, "environment_allowlist")
    _validate_string_list(name, tool, "environment_unset")
    _validate_string_list(name, tool, "redact_environment_values")

    environment = tool.get("environment", {})
    if not isinstance(environment, dict):
        raise ConfigError(f"tool {name!r} environment must be a mapping")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items()):
        raise ConfigError(f"tool {name!r} environment keys and environment values must be strings")

    stdin_mode = tool.get("stdin", "inherit")
    if stdin_mode not in {"inherit", "closed"}:
        raise ConfigError(f"tool {name!r} stdin must be 'inherit' or 'closed'")


def _validate_string_list(name: str, tool: dict[str, Any], key: str) -> None:
    value = tool.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"tool {name!r} {key} must be a list of nonempty strings")


def _validate_cli_router_requirement(data: dict[str, Any], source: Path | None) -> None:
    requirement = data.get("requires_cli_router")
    if requirement is None:
        if data.get("version", 1) == CURRENT_CONFIG_VERSION:
            location = f" in {source}" if source else ""
            raise ConfigError(
                f"Config version {CURRENT_CONFIG_VERSION}{location} requires requires_cli_router"
            )
        return
    location = f" in {source}" if source else ""
    if not isinstance(requirement, str) or not requirement:
        raise ConfigError(f"requires_cli_router{location} must be a nonempty version specifier")
    try:
        specifier = SpecifierSet(requirement)
    except InvalidSpecifier as exc:
        raise ConfigError(f"Invalid requires_cli_router{location}: {requirement!r}") from exc
    if Version(__version__) not in specifier:
        raise ConfigError(
            f"Config{location} requires cli-router {requirement}, but running {__version__}"
        )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
