"""Outlier flags. Raw values are always retained; nothing is dropped or silently winsorized."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.features.engine import FeatureSet, YearMetrics
from avalue_screen.utils import is_finite


@dataclass
class OutlierFlag:
    check_id: str
    period: str | None
    message: str
    value: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


def run_outlier_checks(features: FeatureSet, config: ScreenConfig) -> list[OutlierFlag]:
    flags: list[OutlierFlag] = []
    shock = float(config.require("data_quality.yoy_shock_threshold"))
    share_th = float(config.require("data_quality.share_change_flag_threshold"))
    gm_min = float(config.require("data_quality.margin_reasonable_min"))
    gm_max = float(config.require("data_quality.margin_reasonable_max"))
    roic_min = float(config.require("data_quality.roic_reasonable_min"))
    roic_max = float(config.require("data_quality.roic_reasonable_max"))

    def yoy(name: str) -> None:
        vals = features.series.get(name) or []
        years = features.windows.get("annual_years") or []
        for i in range(1, len(vals)):
            a, b = vals[i - 1], vals[i]
            if is_finite(a) and is_finite(b) and a not in (0, None):
                chg = b / a - 1.0  # type: ignore[operator]
                if abs(chg) > shock:
                    period = str(years[i]) if i < len(years) else None
                    flags.append(
                        OutlierFlag(
                            check_id="DQ-OUTLIER-YOY",
                            period=period,
                            message=f"{name} changed by {chg:.1%}",
                            value=chg,
                            details={"field": name, "prior": a, "current": b},
                        )
                    )

    for name in ("revenue", "net_income_parent", "cash_flow_operating", "FCF"):
        yoy(name)

    for ym in features.annual:
        _flag_range(flags, ym, "gross_margin", gm_min, gm_max)
        _flag_range(flags, ym, "operating_margin", gm_min, gm_max)
        _flag_range(flags, ym, "ROIC", roic_min, roic_max)
        _flag_range(flags, ym, "ROE", roic_min, roic_max)
        for field in ("capex", "depreciation_amortization", "inventory", "trade_receivables"):
            mv = ym.metrics.get(field)
            val = mv.value if mv else ym.fields.get(field)
            if is_finite(val) and val < 0:  # type: ignore[operator]
                flags.append(
                    OutlierFlag(
                        check_id="DQ-NEGATIVE-STOCK-OR-CAPEX",
                        period=str(ym.fiscal_year),
                        message=f"{field} is negative",
                        value=val,
                        details={"field": field},
                    )
                )

    dil = features.market.get("dilution_3y")
    # Annual share change is also flagged in market path when year-on-year change exceeds 10%.
    path = features.market.get("_share_path")
    if path and path.inputs.get("points"):
        pts = path.inputs["points"]
        # not annualized here; 3y dilution already captured
        if dil and is_finite(dil.value) and abs(dil.value) > share_th:
            flags.append(
                OutlierFlag(
                    check_id="DQ-SHARE-CHANGE",
                    period="3y",
                    message=f"Total shares changed by {dil.value:.1%} over 3 years",
                    value=dil.value,
                )
            )
    return flags


def _flag_range(flags: list[OutlierFlag], ym: YearMetrics, name: str, lo: float, hi: float) -> None:
    mv = ym.metrics.get(name)
    val = mv.value if mv else None
    if is_finite(val) and (val < lo or val > hi):  # type: ignore[operator]
        flags.append(
            OutlierFlag(
                check_id="DQ-RATIO-RANGE",
                period=str(ym.fiscal_year),
                message=f"{name} {val:.4f} is outside the configured reasonable range [{lo}, {hi}]",
                value=val,
                details={"field": name, "min": lo, "max": hi},
            )
        )
