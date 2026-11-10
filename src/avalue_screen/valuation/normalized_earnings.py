"""Normalized earnings / FCF / EBIT. Cyclical names use mid-cycle (median) levels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.models import CompanyInput
from avalue_screen.features.engine import FeatureSet
from avalue_screen.utils import is_finite, median


@dataclass
class NormalizedEarnings:
    normalized_ebit: float | None
    normalized_net_income_parent: float | None
    normalized_fcf: float | None
    normalized_fcff: float | None
    normalized_nopat: float | None
    method: str
    is_cyclical: bool
    flags: list[str]
    inputs: dict[str, Any]


def _is_cyclical(company: CompanyInput, config: ScreenConfig) -> bool:
    if company.market.is_cyclical is True:
        return True
    if company.market.is_cyclical is False:
        return False
    cyclical = {str(x).lower() for x in (config.get("cyclical_sectors") or [])}
    # sector overrides file may be merged or not; also look at a well-known list in default via optional key
    sector = (company.market.sector or "").lower()
    industry = (company.market.industry or "").lower()
    return sector in cyclical or industry in cyclical


def _prefer_median(annual_median: float | None, ttm: float | None, *, cyclical: bool) -> tuple[float | None, str]:
    if cyclical:
        if is_finite(annual_median):
            return annual_median, "midcycle_5y_median"
        if is_finite(ttm):
            return ttm, "ttm_fallback"
        return None, "missing"
    # Non-cyclical: prefer comparable, non-outlier complete-year median; TTM is a cross-check.
    if is_finite(annual_median):
        return annual_median, "5y_complete_year_median"
    if is_finite(ttm):
        return ttm, "ttm_fallback"
    return None, "missing"


def normalize_earnings(
    company: CompanyInput,
    features: FeatureSet,
    config: ScreenConfig,
    *,
    sector_cyclical: bool | None = None,
) -> NormalizedEarnings:
    cyclical = _is_cyclical(company, config) if sector_cyclical is None else sector_cyclical
    flags: list[str] = []
    if cyclical:
        flags.append("CYCLICAL_MIDCYCLE")

    w = features.windows
    ttm = features.ttm

    def ttm_val(name: str) -> float | None:
        mv = ttm.get(name)
        return mv.value if mv and is_finite(mv.value) else None

    ebit, ebit_m = _prefer_median(w.get("y5_EBIT_adjusted_median"), ttm_val("EBIT_adjusted"), cyclical=cyclical)
    ni, ni_m = _prefer_median(w.get("y5_net_income_parent_median"), ttm_val("net_income_parent"), cyclical=cyclical)
    fcf, fcf_m = _prefer_median(w.get("y5_FCF_median"), ttm_val("FCF"), cyclical=cyclical)
    fcff, fcff_m = _prefer_median(w.get("y5_FCFF_median"), ttm_val("FCFF"), cyclical=cyclical)
    nopat, nopat_m = _prefer_median(w.get("y5_NOPAT_median"), ttm_val("NOPAT"), cyclical=cyclical)

    method = "midcycle_5y_median" if cyclical else "5y_complete_year_median_with_ttm_check"
    return NormalizedEarnings(
        normalized_ebit=ebit,
        normalized_net_income_parent=ni,
        normalized_fcf=fcf,
        normalized_fcff=fcff,
        normalized_nopat=nopat,
        method=method,
        is_cyclical=cyclical,
        flags=flags,
        inputs={
            "ebit_method": ebit_m,
            "ni_method": ni_m,
            "fcf_method": fcf_m,
            "fcff_method": fcff_m,
            "y5_EBIT_median": w.get("y5_EBIT_adjusted_median"),
            "ttm_EBIT": ttm_val("EBIT_adjusted"),
            "y5_FCF_median": w.get("y5_FCF_median"),
            "ttm_FCF": ttm_val("FCF"),
        },
    )
