"""Cross-check valuation multiples. Negative denominators are never ranked as cheap."""

from __future__ import annotations

from dataclasses import dataclass, field

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.models import CompanyInput
from avalue_screen.features.engine import FeatureSet
from avalue_screen.utils import is_finite
from avalue_screen.valuation.normalized_earnings import NormalizedEarnings
from avalue_screen.features import per_share as ps


@dataclass
class MultiplesResult:
    normalized_pe: float | None
    ev_ebit: float | None
    fcf_yield: float | None
    ev_ebit_discount: float | None
    reasonable_ev_ebit: float | None
    flags: list[str] = field(default_factory=list)


def compute_multiples(
    features: FeatureSet,
    normalized: NormalizedEarnings,
    config: ScreenConfig,
    company: CompanyInput,
) -> MultiplesResult:
    flags: list[str] = []
    mcap = features.market.get("market_cap").value if features.market.get("market_cap") else None
    ev = features.market.get("enterprise_value").value if features.market.get("enterprise_value") else None

    pe_mv = ps.multiple(mcap, normalized.normalized_net_income_parent, "normalized_pe", config, window="normalized")
    ev_mv = ps.multiple(ev, normalized.normalized_ebit, "EV_EBIT", config, window="normalized")
    fy = None
    if is_finite(normalized.normalized_fcf) and is_finite(mcap) and mcap not in (0, None):
        if normalized.normalized_fcf is not None and normalized.normalized_fcf <= 0:
            flags.append("NA_NEGATIVE_DENOMINATOR")
            fy = None
        else:
            fy = normalized.normalized_fcf / mcap  # type: ignore[operator]
    elif is_finite(normalized.normalized_fcf) and normalized.normalized_fcf is not None and normalized.normalized_fcf <= 0:
        flags.append("NA_NEGATIVE_DENOMINATOR")

    if pe_mv.status.value == "NA_NEGATIVE_DENOMINATOR":
        flags.append("NA_NEGATIVE_DENOMINATOR")
    if ev_mv.status.value == "NA_NEGATIVE_DENOMINATOR":
        flags.append("NA_NEGATIVE_DENOMINATOR")

    reasonable = company.market.reasonable_ev_ebit or company.market.peer_ev_ebit_median
    # Own-history EV/EBIT if we can form it from mid-cycle EBIT and current-like capital structure is not used
    # as a substitute for a consistent peer set unless provided. Optionally a config default may be enabled.
    if not is_finite(reasonable) and bool(config.get("valuation.allow_default_reasonable_ev_ebit", False)):
        reasonable = float(config.require("valuation.default_reasonable_ev_ebit"))
        flags.append("EV_EBIT_REASONABLE_DEFAULT")

    discount = None
    if is_finite(ev_mv.value) and is_finite(reasonable) and reasonable not in (0, None):
        # discount >= 30% means current EV/EBIT is 30% cheaper than reasonable, i.e.
        # 1 - ev_ebit / reasonable
        discount = 1.0 - ev_mv.value / reasonable  # type: ignore[operator]
    return MultiplesResult(
        normalized_pe=pe_mv.value if pe_mv.is_valid else None,
        ev_ebit=ev_mv.value if ev_mv.is_valid else None,
        fcf_yield=fy,
        ev_ebit_discount=discount,
        reasonable_ev_ebit=reasonable,
        flags=flags,
    )
