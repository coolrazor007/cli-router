"""Runtime cache of discovered model lists.

The doctor writes healed model lists here so they survive across runs without
editing source. Discovery layers this cache between live CLI discovery and the
static ``DEFAULT_MODELS`` fallback:

    live CLI parse  ->  model-cache.yaml  ->  DEFAULT_MODELS (static)

File format::

    version: 1
    models:
      grok: [grok-4.5, grok-composer-2.5-fast]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


def model_cache_path() -> Path:
    return Path.home() / ".cli-router" / "model-cache.yaml"


@dataclass
class ModelCache:
    models: dict[str, list[str]] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ModelCache":
        target = Path(path) if path else model_cache_path()
        if not target.exists():
            return cls({}, target)
        try:
            data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            # A corrupt cache must never break discovery — treat it as empty.
            return cls({}, target)
        raw = data.get("models", {}) if isinstance(data, dict) else {}
        models: dict[str, list[str]] = {}
        if isinstance(raw, dict):
            for provider, items in raw.items():
                if isinstance(items, list):
                    cleaned = [str(item) for item in items if isinstance(item, str) and item.strip()]
                    if cleaned:
                        models[str(provider)] = cleaned
        return cls(models, target)

    def get(self, provider: str) -> list[str]:
        return list(self.models.get(provider, []))

    def set(self, provider: str, models: list[str]) -> None:
        cleaned = list(dict.fromkeys(model for model in models if model))
        if cleaned:
            self.models[provider] = cleaned
        else:
            self.models.pop(provider, None)

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else (self.path or model_cache_path())
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "models": self.models}
        target.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
        self.path = target
        return target
