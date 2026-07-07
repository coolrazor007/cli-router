"""Configuration loading and validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """Raised when CLI-Router configuration is invalid."""


@dataclass(frozen=True)
class RouterConfig:
    data: dict[str, Any]
    source: Path | None

    @property
    def defaults(self) -> dict[str, Any]:
        return self.data.setdefault("defaults", {})

    @property
    def tools(self) -> dict[str, Any]:
        return self.data.setdefault("tools", {})

    @property
    def workflows(self) -> dict[str, Any]:
        return self.data.setdefault("workflows", {})


CONFIG_CANDIDATES = (
    Path("cli-router.yaml"),
    Path(".cli-router.yaml"),
    Path.home() / ".config" / "cli-router" / "config.yaml",
)


def load_config(path: str | Path | None = None) -> RouterConfig:
    source = Path(path) if path else _find_config()
    config = _built_in_config()

    if source:
        user_config = _read_yaml(source)
        _validate_version(user_config, source)
        config = _deep_merge(config, user_config)

    _validate_config(config, source)
    return RouterConfig(config, source)


def config_to_yaml(config: RouterConfig) -> str:
    return yaml.safe_dump(config.data, sort_keys=False)


def _find_config() -> Path | None:
    for candidate in CONFIG_CANDIDATES:
        if candidate.exists():
            return candidate.resolve()
    return None


def _built_in_config() -> dict[str, Any]:
    text = resources.files("cli_router.presets").joinpath("generic.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    _validate_version(data, None)
    return data


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Could not read config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"Config {path} must contain a YAML mapping")
    return loaded


def _validate_version(data: dict[str, Any], source: Path | None) -> None:
    version = data.get("version", 1)
    if version != 1:
        location = f" in {source}" if source else ""
        raise ConfigError(f"Unsupported config version{location}: {version}")


def _validate_config(data: dict[str, Any], source: Path | None) -> None:
    _validate_version(data, source)
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

    for name, workflow in data.get("workflows", {}).items():
        if not isinstance(workflow, dict):
            raise ConfigError(f"workflow {name!r} must be a mapping")
        stages = workflow.get("stages", [])
        if not isinstance(stages, list):
            raise ConfigError(f"workflow {name!r} stages must be a list")
        for stage in stages:
            if not isinstance(stage, dict):
                raise ConfigError(f"workflow {name!r} stage must be a mapping")
            if "id" not in stage or "tool" not in stage:
                raise ConfigError(f"workflow {name!r} stage is missing id or tool")
            if stage["tool"] not in data.get("tools", {}):
                raise ConfigError(f"workflow {name!r} references unknown tool {stage['tool']!r}")
            fallback_tools = stage.get("fallback_tools", [])
            if not isinstance(fallback_tools, list):
                raise ConfigError(f"workflow {name!r} stage fallback_tools must be a list")
            for fallback_tool in fallback_tools:
                if fallback_tool not in data.get("tools", {}):
                    raise ConfigError(f"workflow {name!r} references unknown fallback tool {fallback_tool!r}")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
