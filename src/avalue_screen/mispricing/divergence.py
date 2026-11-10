"""Price vs fundamental divergence. A price drop alone is never sufficient evidence."""

from __future__ import annotations

from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.features.engine import FeatureSet
from avalue_screen.utils import is_finite
from avalue_screen.valuation.normalized_earnings import NormalizedEarnings


def _change(current: float | None, prior: float | None) -> float | None:
    if not is_finite(current) or not is_finite(prior) or prior in (0, None):
        return None
    return current / prior - 1.0  # type: ignore[operator]


def compute_divergence(
    features: FeatureSet,
    normalized: NormalizedEarnings,
    config: ScreenConfig,
) -> dict[str, Any]:
    """Compare 12m total return with 3-year standardized earnings / FCF / ROIC / revenue / net debt.

    fundamental_decline is a conservative blended deterioration measure used by scoring:
    a positive number means fundamentals declined. Price drops without this comparison
    do not produce a high mispricing score.
    """
    w = features.windows
    ret = features.market.get("price_12m_total_return")
    ret_v = ret.value if ret and is_finite(ret.value) else None
    mcap_dd = features.market.get("market_cap_drawdown_3y")
    dd = mcap_dd.value if mcap_dd and is_finite(mcap_dd.value) else None

    # 3-year changes from annual series (first to last of last 3 years)
    def series_change(key: str) -> float | None:
        vals = w.get(key) or []
        valid = [(i, v) for i, v in enumerate(vals) if is_finite(v)]
        if len(valid) < 2:
            return None
        return _change(valid[-1][1], valid[0][1])

    ni_chg = series_change("y3_net_income_parent_values")
    if ni_chg is None:
        ni_chg = series_change("y3_net_income_ex_nonrecurring_parent_values")
    fcf_chg = series_change("y3_FCF_values")
    rev_chg = series_change("y3_revenue_values")
    roic_vals = w.get("y3_ROIC_values") or []
    roic_chg = None
    roic_valid = [v for v in roic_vals if is_finite(v)]
    if len(roic_valid) >= 2:
        roic_chg = roic_valid[-1] - roic_valid[0]
    nd_chg = series_change("y3_net_debt_values")

    # Blended fundamental decline: worst of standardized earnings/FCF decline (positive = worse)
    # For ROIC, a drop of X (e.g. -0.05) is a 5pp decline; convert to a comparable 'decline' fraction
    # by using the max of NI/FCF revenue declines (sign-flipped).
    candidates = []
    for chg in (ni_chg, fcf_chg, rev_chg):
        if is_finite(chg):
            candidates.append(-chg)  # type: ignore[operator]
    if is_finite(roic_chg):
        # 5pp ROIC decline counts as 0.05 decline
        candidates.append(-roic_chg)  # type: ignore[operator]
    fund_decline = max(candidates) if candidates else None
    # If everything improved, fund_decline is negative; clip at 0 for "decline" language but keep signed
    # Scoring uses the signed decline (positive = deteriorated).

    trig = float(config.require("mispricing.price_drawdown_trigger_12m"))
    strong = float(config.require("mispricing.price_drawdown_strong_12m"))
    mcap_trig = float(config.require("mispricing.market_cap_drawdown_3y_trigger"))
    queue = False
    triggers: list[str] = []
    if is_finite(ret_v) and ret_v <= trig:  # type: ignore[operator]
        queue = True
        triggers.append("price_12m_total_return")
    if is_finite(dd) and dd >= mcap_trig:  # type: ignore[operator]
        queue = True
        triggers.append("market_cap_drawdown_3y")

    score = None
    if is_finite(ret_v) and fund_decline is not None:
        # A simple 0-4 diagnostic, not the official item score (official score lives in scoring.py)
        max_d = float(config.require("mispricing.max_fundamental_decline_for_strong_divergence"))
        if ret_v <= strong and fund_decline <= max_d:
            score = 4
        elif ret_v <= trig and fund_decline <= max_d:
            score = 2
        else:
            score = 0

    return {
        "price_12m_total_return": ret_v,
        "market_cap_drawdown_3y": dd,
        "ni_3y_change": ni_chg,
        "fcf_3y_change": fcf_chg,
        "revenue_3y_change": rev_chg,
        "roic_3y_change": roic_chg,
        "net_debt_3y_change": nd_chg,
        "fundamental_decline": fund_decline,
        "price_fundamental_divergence_score": score,
        "research_queue": queue,
        "triggers": triggers,
        "notes": "A price decline alone is not mispricing evidence; standardized fundamental change is required.",
    }
