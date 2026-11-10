"""Profitability formulas: EBIT, tax, NOPAT, ROIC, ROE, margins."""

from __future__ import annotations

from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import MetricStatus
from avalue_screen.domain.models import MetricValue
from avalue_screen.utils import is_finite, median, safe_div


NON_OPERATING_GAIN_FIELDS = (
    "investment_income",
    "fair_value_change",
    "asset_disposal_gain",
)


def ebit_adjusted(
    fields: dict[str, float | None],
    config: ScreenConfig,
    *,
    window: str,
    recurring_non_operating: bool = False,
) -> MetricValue:
    version = config.formula_version("EBIT_adjusted")
    flags: list[str] = []
    status = MetricStatus.OK
    if is_finite(fields.get("EBIT_adjusted")):
        return MetricValue(
            name="EBIT_adjusted",
            value=fields.get("EBIT_adjusted"),
            status=MetricStatus.OK,
            window=window,
            formula_id="EBIT_adjusted",
            formula_version=version,
            inputs={"source": "vendor_EBIT_adjusted"},
            notes="Vendor-provided EBIT_adjusted used as-is",
        )
    op = fields.get("operating_profit")
    interest_exp = fields.get("interest_expense_gross")
    interest_inc = fields.get("interest_income")
    if not is_finite(op):
        return MetricValue(
            name="EBIT_adjusted",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="EBIT_adjusted",
            formula_version=version,
            inputs={"operating_profit": op},
            notes="operating_profit missing; EBIT cannot be derived",
        )
    missing_interest = not is_finite(interest_exp)
    if missing_interest:
        interest_exp = 0.0
        flags.append("INTEREST_EXPENSE_ASSUMED_ZERO_FOR_ESTIMATE")
        status = MetricStatus.ESTIMATED
    if not is_finite(interest_inc):
        interest_inc = 0.0
        flags.append("INTEREST_INCOME_ASSUMED_ZERO_FOR_ESTIMATE")
        status = MetricStatus.ESTIMATED

    exclude = 0.0
    exclude_detail: dict[str, float] = {}
    missing_nonop = False
    if not recurring_non_operating:
        for name in NON_OPERATING_GAIN_FIELDS:
            val = fields.get(name)
            if val is None:
                missing_nonop = True
                continue
            exclude += val
            exclude_detail[name] = val
        if missing_nonop:
            flags.append("NON_OPERATING_ITEMS_PARTIAL")
            status = MetricStatus.ESTIMATED
    value = op + interest_exp - interest_inc - exclude  # type: ignore[operator]
    return MetricValue(
        name="EBIT_adjusted",
        value=value,
        status=status,
        window=window,
        formula_id="EBIT_adjusted",
        formula_version=version,
        inputs={
            "operating_profit": op,
            "interest_expense_gross": fields.get("interest_expense_gross"),
            "interest_income": fields.get("interest_income"),
            "non_operating_gains_excluded": exclude_detail,
        },
        flags=flags,
    )


def effective_tax_rate(
    income_tax_expense: float | None,
    profit_before_tax: float | None,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    clip_min = float(config.require("valuation.tax_rate_clip_min"))
    clip_max = float(config.require("valuation.tax_rate_clip_max"))
    if not is_finite(income_tax_expense) or not is_finite(profit_before_tax):
        return MetricValue(
            name="ETR",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="ETR",
            formula_version="etr_v1",
        )
    if profit_before_tax <= 0:  # type: ignore[operator]
        return MetricValue(
            name="ETR",
            value=None,
            status=MetricStatus.INVALID,
            window=window,
            formula_id="ETR",
            formula_version="etr_v1",
            notes="ETR only computed when profit_before_tax > 0",
            inputs={"income_tax_expense": income_tax_expense, "profit_before_tax": profit_before_tax},
        )
    rate = income_tax_expense / profit_before_tax  # type: ignore[operator]
    if rate < clip_min or rate > clip_max:
        return MetricValue(
            name="ETR",
            value=rate,
            status=MetricStatus.INVALID,
            window=window,
            formula_id="ETR",
            formula_version="etr_v1",
            notes=f"ETR {rate:.4f} outside reasonable interval [{clip_min}, {clip_max}]",
            inputs={"income_tax_expense": income_tax_expense, "profit_before_tax": profit_before_tax},
        )
    return MetricValue(
        name="ETR",
        value=rate,
        status=MetricStatus.OK,
        window=window,
        formula_id="ETR",
        formula_version="etr_v1",
        inputs={"income_tax_expense": income_tax_expense, "profit_before_tax": profit_before_tax},
    )


def normalized_tax_rate(
    valid_etrs: list[float],
    config: ScreenConfig,
    *,
    sector_rate: float | None = None,
) -> MetricValue:
    clip_min = float(config.require("valuation.tax_rate_clip_min"))
    clip_max = float(config.require("valuation.tax_rate_clip_max"))
    min_years = int(config.require("valuation.tax_rate_min_valid_years"))
    fallback = float(config.require("valuation.tax_rate_fallback"))
    flags: list[str] = []
    status = MetricStatus.OK
    if len(valid_etrs) >= min_years:
        rate = median(valid_etrs)
        notes = f"Median of {len(valid_etrs)} valid fiscal-year ETRs"
    elif sector_rate is not None:
        rate = sector_rate
        status = MetricStatus.TAX_RATE_FALLBACK
        flags.append("TAX_RATE_FALLBACK")
        notes = "Fewer than 2 valid ETRs; using configured sector/company tax rate"
    else:
        rate = fallback
        status = MetricStatus.TAX_RATE_FALLBACK
        flags.append("TAX_RATE_FALLBACK")
        notes = "Fewer than 2 valid ETRs and no sector rate; using default  statutory-like fallback"
    if rate is not None:
        rate = min(max(rate, clip_min), clip_max)
    return MetricValue(
        name="normalized_tax_rate",
        value=rate,
        status=status,
        window="5y_median",
        formula_id="NOPAT",
        formula_version=config.formula_version("NOPAT"),
        inputs={"valid_etrs": valid_etrs, "sector_rate": sector_rate},
        flags=flags,
        notes=notes,
    )


def nopat(ebit: float | None, tax_rate: float | None, config: ScreenConfig, *, window: str) -> MetricValue:
    if not is_finite(ebit) or not is_finite(tax_rate):
        return MetricValue(
            name="NOPAT",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="NOPAT",
            formula_version=config.formula_version("NOPAT"),
        )
    return MetricValue(
        name="NOPAT",
        value=ebit * (1.0 - tax_rate),  # type: ignore[operator]
        status=MetricStatus.OK,
        window=window,
        formula_id="NOPAT",
        formula_version=config.formula_version("NOPAT"),
        inputs={"EBIT_adjusted": ebit, "normalized_tax_rate": tax_rate},
    )


def margin(numer: float | None, revenue: float | None, name: str, config: ScreenConfig, *, window: str) -> MetricValue:
    if not is_finite(numer) or not is_finite(revenue):
        return MetricValue(name=name, value=None, status=MetricStatus.MISSING, window=window)
    if revenue is None or revenue <= 0:
        return MetricValue(
            name=name,
            value=None,
            status=MetricStatus.INVALID,
            window=window,
            notes="Revenue <= 0; margin not computed to avoid a spurious high ratio",
            inputs={"numerator": numer, "revenue": revenue},
        )
    return MetricValue(
        name=name,
        value=numer / revenue,
        status=MetricStatus.OK,
        window=window,
        inputs={"numerator": numer, "revenue": revenue},
    )


def roic_from_parts(
    nopat_ttm: float | None,
    invested_capital_t: float | None,
    invested_capital_t1: float | None,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    version = config.formula_version("ROIC")
    if not is_finite(nopat_ttm) or not is_finite(invested_capital_t) or not is_finite(invested_capital_t1):
        return MetricValue(
            name="ROIC",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="ROIC",
            formula_version=version,
            inputs={
                "NOPAT_TTM": nopat_ttm,
                "invested_capital_t": invested_capital_t,
                "invested_capital_t-1": invested_capital_t1,
            },
        )
    avg_ic = (invested_capital_t + invested_capital_t1) / 2.0  # type: ignore[operator]
    if avg_ic <= 0:
        return MetricValue(
            name="ROIC",
            value=None,
            status=MetricStatus.NA_NONPOSITIVE_CAPITAL,
            window=window,
            formula_id="ROIC",
            formula_version=version,
            inputs={"NOPAT_TTM": nopat_ttm, "average_invested_capital": avg_ic},
            notes="Average invested capital <= 0; ROIC is invalid and must not be scored",
        )
    return MetricValue(
        name="ROIC",
        value=nopat_ttm / avg_ic,
        status=MetricStatus.OK,
        window=window,
        formula_id="ROIC",
        formula_version=version,
        inputs={"NOPAT_TTM": nopat_ttm, "average_invested_capital": avg_ic},
    )


def roe_from_parts(
    ni_parent_ttm: float | None,
    equity_t: float | None,
    equity_t1: float | None,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    version = config.formula_version("ROE")
    if not is_finite(ni_parent_ttm) or not is_finite(equity_t) or not is_finite(equity_t1):
        return MetricValue(name="ROE", value=None, status=MetricStatus.MISSING, window=window, formula_id="ROE", formula_version=version)
    avg = (equity_t + equity_t1) / 2.0  # type: ignore[operator]
    if avg <= 0:
        return MetricValue(
            name="ROE",
            value=None,
            status=MetricStatus.NA_NONPOSITIVE_CAPITAL,
            window=window,
            formula_id="ROE",
            formula_version=version,
            notes="Average parent equity <= 0; ROE is invalid",
            inputs={"net_income_parent_TTM": ni_parent_ttm, "average_equity_parent": avg},
        )
    return MetricValue(
        name="ROE",
        value=ni_parent_ttm / avg,
        status=MetricStatus.OK,
        window=window,
        formula_id="ROE",
        formula_version=version,
        inputs={"net_income_parent_TTM": ni_parent_ttm, "average_equity_parent": avg},
    )
