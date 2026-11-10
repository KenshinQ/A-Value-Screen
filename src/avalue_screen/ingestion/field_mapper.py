"""Map vendor field names onto the standard dictionary. No investment judgment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from avalue_screen.domain.models import FieldValue, FinancialStatement


class FieldMapper:
    def __init__(self, mapping: dict[str, Any] | None = None) -> None:
        mapping = mapping or {}
        self.aliases: dict[str, str] = {}
        raw_aliases = mapping.get("aliases") or {}
        for standard, names in raw_aliases.items():
            self.aliases[standard.lower()] = standard
            for name in names or []:
                self.aliases[str(name).lower()] = standard
        self.already_included_in: dict[str, list[str]] = mapping.get("already_included_in") or {}
        self.notes_payable_interest_bearing = bool(
            mapping.get("notes_payable_default_interest_bearing", False)
        )

    @classmethod
    def from_path(cls, path: str | Path) -> "FieldMapper":
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(data)

    def standard_name(self, name: str) -> str:
        return self.aliases.get(name.lower(), name)

    def map_block(self, block: dict[str, Any]) -> dict[str, FieldValue]:
        out: dict[str, FieldValue] = {}
        for raw_name, raw_value in (block or {}).items():
            std = self.standard_name(str(raw_name))
            fv = _coerce_field(raw_value)
            out[std] = fv
        return out

    def map_statement(self, stmt: FinancialStatement) -> FinancialStatement:
        return stmt.model_copy(
            update={
                "income": self.map_block(stmt.income),
                "balance": self.map_block(stmt.balance),
                "cashflow": self.map_block(stmt.cashflow),
            }
        )


def _coerce_field(raw_value: Any) -> FieldValue:
    if isinstance(raw_value, FieldValue):
        return raw_value
    if isinstance(raw_value, dict):
        return FieldValue.model_validate(raw_value)
    if raw_value is None or isinstance(raw_value, (int, float)):
        return FieldValue(value=None if raw_value is None else float(raw_value))
    raise TypeError(f"Cannot coerce field value: {type(raw_value)}")
