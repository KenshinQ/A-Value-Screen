"""Univariate reverse DCF: implied FCFF growth that equates equity value to market cap.

Never clamps a missing root to the search boundary; reports BELOW/ABOVE_SEARCH_RANGE instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import ReverseDcfStatus
from avalue_screen.utils import is_finite


@dataclass
class ReverseDcfResult:
    status: ReverseDcfStatus
    g_implied: float | None
    equity_at_g_min: float | None = None
    equity_at_g_max: float | None = None
    market_cap: float | None = None
    iterations: int = 0


def equity_value_at_g(
    g: float,
    fcff0: float,
    wacc: float,
    terminal_growth: float,
    years: int,
    net_debt: float,
    minority_interest: float,
    eligible_non_operating: float,
) -> float:
    fcffs = [fcff0 * (1.0 + g) ** t for t in range(1, years + 1)]
    fcff_n1 = fcffs[-1] * (1.0 + terminal_growth)
    tv = fcff_n1 / (wacc - terminal_growth)
    ev = sum(fcffs[t] / (1.0 + wacc) ** (t + 1) for t in range(years)) + tv / (1.0 + wacc) ** years
    return ev - net_debt - minority_interest + eligible_non_operating


def solve_reverse_dcf(
    *,
    fcff0: float,
    wacc: float,
    terminal_growth: float,
    years: int,
    market_cap: float | None,
    net_debt: float,
    minority_interest: float,
    eligible_non_operating: float,
    config: ScreenConfig,
) -> ReverseDcfResult:
    if not is_finite(fcff0) or fcff0 <= 0:
        return ReverseDcfResult(status=ReverseDcfStatus.UNAVAILABLE_NONPOSITIVE_FCF, g_implied=None)
    if not is_finite(market_cap) or market_cap is None or market_cap <= 0:
        return ReverseDcfResult(status=ReverseDcfStatus.UNAVAILABLE, g_implied=None, market_cap=market_cap)
    if wacc <= terminal_growth:
        return ReverseDcfResult(status=ReverseDcfStatus.UNAVAILABLE, g_implied=None, market_cap=market_cap)

    g_min = float(config.require("valuation.reverse_dcf_g_min"))
    g_max = float(config.require("valuation.reverse_dcf_g_max"))
    tol = float(config.require("valuation.reverse_dcf_tolerance"))
    max_iter = int(config.require("valuation.reverse_dcf_max_iter"))

    def ev(g: float) -> float:
        return equity_value_at_g(
            g, fcff0, wacc, terminal_growth, years, net_debt, minority_interest, eligible_non_operating
        )

    lo_v = ev(g_min)
    hi_v = ev(g_max)
    # Equity value should be increasing in g for fcff0 > 0 and wacc > g_term
    if market_cap < min(lo_v, hi_v) - tol * max(1.0, abs(market_cap)):
        # Below the cheaper bound
        if lo_v <= hi_v:
            return ReverseDcfResult(
                status=ReverseDcfStatus.BELOW_SEARCH_RANGE,
                g_implied=None,
                equity_at_g_min=lo_v,
                equity_at_g_max=hi_v,
                market_cap=market_cap,
            )
        return ReverseDcfResult(status=ReverseDcfStatus.BELOW_SEARCH_RANGE, g_implied=None, equity_at_g_min=lo_v, equity_at_g_max=hi_v, market_cap=market_cap)
    if market_cap > max(lo_v, hi_v) + tol * max(1.0, abs(market_cap)):
        return ReverseDcfResult(
            status=ReverseDcfStatus.ABOVE_SEARCH_RANGE,
            g_implied=None,
            equity_at_g_min=lo_v,
            equity_at_g_max=hi_v,
            market_cap=market_cap,
        )

    low, high = g_min, g_max
    # Ensure low_v <= market_cap <= high_v by swapping if needed
    if ev(low) > ev(high):
        low, high = high, low
    iters = 0
    mid = low
    for iters in range(1, max_iter + 1):
        mid = (low + high) / 2.0
        val = ev(mid)
        if abs(val - market_cap) <= tol * max(1.0, abs(market_cap)):
            return ReverseDcfResult(
                status=ReverseDcfStatus.SOLVED,
                g_implied=mid,
                equity_at_g_min=lo_v,
                equity_at_g_max=hi_v,
                market_cap=market_cap,
                iterations=iters,
            )
        if val < market_cap:
            low = mid
        else:
            high = mid
    return ReverseDcfResult(
        status=ReverseDcfStatus.SOLVED,
        g_implied=mid,
        equity_at_g_min=lo_v,
        equity_at_g_max=hi_v,
        market_cap=market_cap,
        iterations=iters,
    )
