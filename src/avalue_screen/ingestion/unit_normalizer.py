"""Normalize units and currency. Amounts become CNY yuan; ratios become decimals."""

from __future__ import annotations

from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.errors import UnitNormalizationError
from avalue_screen.domain.models import FieldValue


# Fields that are stored as decimal ratios (0.10 = 10%).
RATIO_FIELDS = {
    "gross_margin",
    "operating_margin",
    "roic",
    "roe",
    "etr",
    "beta",
    "yield",
}

PERCENT_UNITS = {"percent", "pct", "%", "percentage"}


def normalize_numeric(
    value: float | None,
    unit: str | None,
    config: ScreenConfig,
    *,
    field: str | None = None,
    currency: str | None = None,
) -> float | None:
    if value is None:
        return None
    units: dict[str, float] = config.require("normalization.units")
    default_currency = config.get("normalization.default_currency", "CNY")
    currency = (currency or default_currency).upper()
    if currency not in {"CNY", "RMB", "CNH"}:
        raise UnitNormalizationError(
            f"Currency '{currency}' cannot be auto-converted in v0.1; amounts must be CNY"
        )
    unit_key = (unit or config.get("normalization.default_amount_unit", "yuan") or "yuan").strip().lower()
    unit_key = {
        "cny": "yuan",
        "rmb": "yuan",
        "元": "yuan",
        "千元": "thousand_yuan",
        "万元": "ten_thousand_yuan",
        "百万元": "million_yuan",
        "百万": "million_yuan",
        "股": "share",
        "%": "percent",
        "pct": "percent",
        "percentage": "percent",
    }.get(unit_key, unit_key)
    if unit_key not in units:
        raise UnitNormalizationError(f"Unknown unit '{unit}' for field '{field}'")
    factor = float(units[unit_key])
    return value * factor


def normalize_field_value(
    fv: FieldValue,
    config: ScreenConfig,
    *,
    field: str | None = None,
) -> FieldValue:
    """Return a new FieldValue in standard units. Does not silently fill missing values with 0."""
    if fv.value is None:
        return fv.model_copy(update={"currency": "CNY"})
    unit = fv.unit
    new_unit = "yuan"
    if (unit or "").lower() in {"share", "股"}:
        new_unit = "share"
        normalized = normalize_numeric(fv.value, unit, config, field=field, currency=fv.currency)
    elif (unit or "").lower() in PERCENT_UNITS or (field or "") in RATIO_FIELDS:
        # percent 10 -> 0.10; already-decimal 'ratio' stays as-is
        if (unit or "").lower() in PERCENT_UNITS:
            normalized = fv.value * float(config.require("normalization.units")["percent"])
        else:
            normalized = fv.value
        new_unit = "ratio"
    elif (unit or "").lower() in {"day", "days"}:
        normalized = fv.value
        new_unit = "day"
    elif (unit or "").lower() in {"ratio"}:
        normalized = fv.value
        new_unit = "ratio"
    else:
        normalized = normalize_numeric(fv.value, unit, config, field=field, currency=fv.currency)
        new_unit = "yuan"
    return fv.model_copy(update={"value": normalized, "unit": new_unit, "currency": "CNY"})
