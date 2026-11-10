"""Load and validate versioned YAML configuration."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from avalue_screen.domain.errors import ConfigError

DEFAULT_CONFIG_NAME = "default.yaml"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config must be a mapping: {path}")
    return data


def load_config(
    path: str | Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    sector_overrides_path: str | Path | None = None,
) -> "ScreenConfig":
    if path is None:
        path = Path(__file__).resolve().parents[2] / "configs" / DEFAULT_CONFIG_NAME
    raw = load_yaml(path)
    if sector_overrides_path:
        raw = _deep_merge(raw, load_yaml(sector_overrides_path))
    if overrides:
        raw = _deep_merge(raw, overrides)
    return ScreenConfig(raw, source_path=str(Path(path).resolve()))


class ScreenConfig:
    """Dictionary-backed config with attribute and path access.

    All thresholds must be read from here. Business logic must not hardcode them.
    """

    def __init__(self, data: dict[str, Any], source_path: str | None = None) -> None:
        self._data = data
        self.source_path = source_path
        self._validate()

    def _validate(self) -> None:
        required = [
            "rule_version",
            "universe",
            "screening",
            "quality_gates",
            "valuation",
            "mispricing",
            "scoring",
            "data_quality",
        ]
        missing = [k for k in required if k not in self._data]
        if missing:
            raise ConfigError(f"Config missing required sections: {missing}")
        universe = self._data["universe"]
        if "main_board_only" not in universe:
            raise ConfigError("universe.main_board_only must be present (project default true; SPEC original default false)")

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @property
    def rule_version(self) -> str:
        return str(self._data.get("rule_version", "0.1.0"))

    def get(self, path: str, default: Any = None) -> Any:
        cur: Any = self._data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def require(self, path: str) -> Any:
        cur: Any = self._data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                raise ConfigError(f"Missing required config path: {path}")
            cur = cur[part]
        return cur

    def section(self, name: str) -> dict[str, Any]:
        value = self.require(name)
        if not isinstance(value, dict):
            raise ConfigError(f"Config section {name} must be a mapping")
        return value

    def formula_version(self, formula_id: str) -> str:
        versions = self._data.get("formula_versions") or {}
        return str(versions.get(formula_id, f"{formula_id}_v1"))

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self._data)
