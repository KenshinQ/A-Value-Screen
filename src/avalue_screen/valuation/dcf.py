"""Three-scenario FCFF DCF, WACC, MOS. Assumptions are always retained."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import MetricStatus, ReverseDcfStatus
from avalue_screen.domain.models import CompanyInput, RiskFreeRatePoint, ValuationResult
from avalue_screen.features.engine import FeatureSet
from avalue_screen.utils import cagr, is_finite, median
from avalue_screen.valuation.normalized_earnings import NormalizedEarnings
from avalue_screen.valuation.reverse_dcf import solve_reverse_dcf
from avalue_screen.valuation.multiples import compute_multiples


def _rf_on_or_before(curve: Iterable[RiskFreeRatePoint], as_of: date) -> RiskFreeRatePoint | None:
    pts = [p for p in curve if p.rate_date <= as_of]
    if not pts:
        return None
    return max(pts, key=lambda p: p.rate_date)


def _last_revenue(features: FeatureSet) -> float | None:
    if features.ttm.get("revenue") and is_finite(features.ttm["revenue"].value):
        return features.ttm["revenue"].value
    if features.annual:
        return features.annual[-1].fields.get("revenue")
    return None


def _cagr_value(mv) -> float | None:
    if mv is None:
        return None
    if hasattr(mv, "is_valid") and mv.is_valid:
        return mv.value
    return None


def forecast_assumptions(features: FeatureSet, config: ScreenConfig) -> dict[str, Any]:
    v = config.section("valuation")
    g3 = _cagr_value(features.windows.get("revenue_cagr_3y"))
    g5 = _cagr_value(features.windows.get("revenue_cagr_5y"))
    flags: list[str] = []
    w3 = float(v["revenue_cagr_3y_weight"])
    w5 = float(v["revenue_cagr_5y_weight"])
    if is_finite(g3) and is_finite(g5):
        base_g = w3 * g3 + w5 * g5  # type: ignore[operator]
    elif is_finite(g3):
        base_g = g3
        flags.append("REVENUE_GROWTH_3Y_ONLY")
    elif is_finite(g5):
        base_g = g5
        flags.append("REVENUE_GROWTH_5Y_ONLY")
    else:
        base_g = None
        flags.append("REVENUE_GROWTH_CAGR_INVALID")

    if base_g is not None:
        lo = float(v["base_revenue_growth_floor"])
        hi = float(v["base_revenue_growth_cap"])
        clipped = min(max(base_g, lo), hi)
        if clipped != base_g:
            flags.append("REVENUE_GROWTH_CLIPPED")
        base_g = clipped
        bear_g = max(base_g + float(v["bear_revenue_growth_delta"]), float(v["bear_revenue_growth_floor"]))
        bull_g = min(base_g + float(v["bull_revenue_growth_delta"]), float(v["bull_revenue_growth_cap"]))
    else:
        bear_g = bull_g = None

    m5 = features.windows.get("y5_operating_margin_median")
    m2 = features.windows.get("y2_operating_margin_median")
    mw5 = float(v["ebit_margin_5y_weight"])
    mw2 = float(v["ebit_margin_2y_weight"])
    if is_finite(m5) and is_finite(m2):
        base_m = mw5 * m5 + mw2 * m2  # type: ignore[operator]
    elif is_finite(m5):
        base_m = m5
    elif is_finite(m2):
        base_m = m2
    else:
        base_m = None
        flags.append("EBIT_MARGIN_UNAVAILABLE")
    if base_m is not None:
        bear_m = base_m + float(v["bear_ebit_margin_delta"])
        bull_m = base_m + float(v["bull_ebit_margin_delta"])
    else:
        bear_m = bull_m = None

    return {
        "base_revenue_growth": base_g,
        "bear_revenue_growth": bear_g,
        "bull_revenue_growth": bull_g,
        "base_ebit_margin": base_m,
        "bear_ebit_margin": bear_m,
        "bull_ebit_margin": bull_m,
        "da_to_revenue": features.windows.get("da_to_revenue_median"),
        "capex_to_revenue": features.windows.get("capex_to_revenue_median"),
        "nwc_to_revenue": features.windows.get("nwc_to_revenue_median"),
        "flags": flags,
    }


def compute_wacc(
    features: FeatureSet,
    company: CompanyInput,
    config: ScreenConfig,
    as_of: date,
    risk_free_curve: Iterable[RiskFreeRatePoint],
) -> dict[str, Any]:
    v = config.section("valuation")
    flags: list[str] = []
    rf_pt = _rf_on_or_before(risk_free_curve, as_of)
    if rf_pt is None:
        rf = float(v.get("risk_free_rate_fallback", 0.025))
        flags.append("RISK_FREE_FALLBACK")
        rf_source = "config_fallback"
        rf_date = None
    else:
        rf = float(rf_pt.yield_10y)
        rf_source = rf_pt.source or v.get("risk_free_rate_source")
        rf_date = rf_pt.rate_date

    beta = company.market.levered_beta
    if not is_finite(beta):
        beta = float(v["beta_fallback"])
        flags.append("BETA_FALLBACK")
    beta = min(max(float(beta), float(v["beta_floor"])), float(v["beta_cap"]))
    erp = float(v["equity_risk_premium"])
    cost_equity = rf + erp * beta

    ttm = features.ttm
    interest = ttm.get("interest_expense_gross").value if ttm.get("interest_expense_gross") else None
    debt = ttm.get("interest_bearing_debt").value if ttm.get("interest_bearing_debt") else None
    # Average debt: use current if prior not separately stored
    avg_debt = debt
    tax = ttm.get("normalized_tax_rate").value if ttm.get("normalized_tax_rate") else float(v["tax_rate_fallback"])
    near_zero = float(config.require("quality_gates.interest_expense_near_zero"))
    if not is_finite(avg_debt) or avg_debt == 0:
        cost_debt = 0.0
        d_weight = 0.0
        e_weight = 1.0
        after_tax_debt = 0.0
        if is_finite(avg_debt) and avg_debt == 0:
            flags.append("ZERO_DEBT_WEIGHT")
        elif not is_finite(avg_debt):
            flags.append("COST_OF_DEBT_FALLBACK")
            cost_debt = float(v["sector_cost_of_debt_fallback"])
            after_tax_debt = cost_debt * (1.0 - (tax or 0.0))
    elif not is_finite(interest):
        cost_debt = float(v["sector_cost_of_debt_fallback"])
        after_tax_debt = cost_debt * (1.0 - (tax or 0.0))
        flags.append("COST_OF_DEBT_FALLBACK")
        mcap = features.market.get("market_cap").value if features.market.get("market_cap") else None
        if is_finite(mcap) and is_finite(avg_debt) and (mcap + avg_debt) not in (0, None):
            e_weight = mcap / (mcap + avg_debt)
            d_weight = avg_debt / (mcap + avg_debt)
        else:
            e_weight, d_weight = 1.0, 0.0
    else:
        if abs(avg_debt) <= near_zero:
            cost_debt = 0.0
            after_tax_debt = 0.0
            d_weight = 0.0
            e_weight = 1.0
        else:
            cost_debt = interest / avg_debt  # type: ignore[operator]
            after_tax_debt = cost_debt * (1.0 - (tax or 0.0))
            mcap = features.market.get("market_cap").value if features.market.get("market_cap") else None
            if is_finite(mcap) and (mcap + avg_debt) not in (0, None):
                e_weight = mcap / (mcap + avg_debt)
                d_weight = avg_debt / (mcap + avg_debt)
            else:
                e_weight, d_weight = 1.0, 0.0
                flags.append("WACC_WEIGHTS_FALLBACK")

    wacc_raw = e_weight * cost_equity + d_weight * after_tax_debt
    floor = float(v["wacc_floor"])
    cap = float(v["wacc_cap"])
    wacc = min(max(wacc_raw, floor), cap)
    if wacc != wacc_raw:
        flags.append("WACC_CLAMPED")
    tg = min(float(v["terminal_growth_default"]), float(v["terminal_growth_cap"]))
    spread = float(v["terminal_spread_min"])
    if wacc < tg + spread:
        wacc = tg + spread
        flags.append("WACC_LIFTED_FOR_TERMINAL_SPREAD")
        if wacc > cap:
            flags.append("WACC_SPREAD_EXCEEDS_CAP")
    return {
        "wacc": wacc,
        "wacc_raw": wacc_raw,
        "cost_of_equity": cost_equity,
        "cost_of_debt": cost_debt,
        "after_tax_cost_of_debt": after_tax_debt,
        "e_weight": e_weight,
        "d_weight": d_weight,
        "levered_beta": beta,
        "risk_free_rate": rf,
        "risk_free_rate_source": rf_source,
        "risk_free_rate_as_of": rf_date,
        "equity_risk_premium": erp,
        "terminal_growth": tg,
        "normalized_tax_rate": tax,
        "flags": flags,
    }


def _project_path(
    revenue0: float,
    nwc0: float | None,
    growth: float,
    margin: float,
    tax: float,
    da_ratio: float,
    capex_ratio: float,
    nwc_ratio: float,
    years: int,
    incremental_roic_cap: float,
) -> dict[str, list[float]]:
    revenues = []
    ebits = []
    nopats = []
    das = []
    capexs = []
    nwcs = []
    deltas = []
    fcffs = []
    prev_rev = revenue0
    prev_nwc = nwc_ratio * revenue0 if nwc0 is None else nwc0
    prev_nopat = revenue0 * margin * (1.0 - tax)
    for t in range(1, years + 1):
        rev = revenue0 * (1.0 + growth) ** t
        ebit = rev * margin
        nopat = ebit * (1.0 - tax)
        da = da_ratio * rev
        capex = capex_ratio * rev
        nwc = nwc_ratio * rev
        delta = nwc - prev_nwc
        # Reinvestment sanity: if growth > 0, implied incremental ROIC must not be absurdly high
        reinvest = capex - da + delta
        d_nopat = nopat - prev_nopat
        if growth > 0 and d_nopat > 0 and incremental_roic_cap > 0:
            min_reinvest = d_nopat / incremental_roic_cap
            if reinvest < min_reinvest:
                extra = min_reinvest - reinvest
                capex = capex + extra
                reinvest = capex - da + delta
        fcff = nopat + da - capex - delta
        revenues.append(rev)
        ebits.append(ebit)
        nopats.append(nopat)
        das.append(da)
        capexs.append(capex)
        nwcs.append(nwc)
        deltas.append(delta)
        fcffs.append(fcff)
        prev_nwc = nwc
        prev_nopat = nopat
        prev_rev = rev
    return {
        "revenue": revenues,
        "ebit": ebits,
        "nopat": nopats,
        "da": das,
        "capex": capexs,
        "nwc": nwcs,
        "delta_nwc": deltas,
        "fcff": fcffs,
    }


def _present_value(fcffs: list[float], wacc: float, tg: float) -> tuple[float, float, float]:
    n = len(fcffs)
    pv_fcff = sum(fcffs[t] / (1.0 + wacc) ** (t + 1) for t in range(n))
    fcff_n1 = fcffs[-1] * (1.0 + tg)
    tv = fcff_n1 / (wacc - tg)
    pv_tv = tv / (1.0 + wacc) ** n
    ev = pv_fcff + pv_tv
    weight = pv_tv / ev if ev not in (0, None) else None
    return ev, pv_tv, weight if weight is not None else 0.0


def _equity_per_share(
    ev: float,
    net_debt: float | None,
    minority: float | None,
    eligible: float | None,
    shares: float | None,
) -> float | None:
    if not is_finite(shares) or shares <= 0:  # type: ignore[operator]
        return None
    equity = ev - (net_debt or 0.0) - (minority or 0.0) + (eligible or 0.0)
    return equity / shares


def run_dcf(
    company: CompanyInput,
    features: FeatureSet,
    normalized: NormalizedEarnings,
    config: ScreenConfig,
    as_of: date,
    risk_free_curve: Iterable[RiskFreeRatePoint],
) -> ValuationResult:
    flags: list[str] = []
    assumptions = forecast_assumptions(features, config)
    flags.extend(assumptions["flags"])
    wacc_info = compute_wacc(features, company, config, as_of, risk_free_curve)
    flags.extend(wacc_info["flags"])

    revenue0 = _last_revenue(features)
    tax = wacc_info["normalized_tax_rate"] or float(config.require("valuation.tax_rate_fallback"))
    da_r = assumptions["da_to_revenue"]
    capex_r = assumptions["capex_to_revenue"]
    nwc_r = assumptions["nwc_to_revenue"]
    years = int(config.require("valuation.forecast_years"))
    incomplete = False
    needed = {
        "revenue0": revenue0,
        "base_revenue_growth": assumptions["base_revenue_growth"],
        "base_ebit_margin": assumptions["base_ebit_margin"],
        "da_to_revenue": da_r,
        "capex_to_revenue": capex_r,
        "nwc_to_revenue": nwc_r,
        "wacc": wacc_info["wacc"],
    }
    if any(not is_finite(v) for v in needed.values()):
        incomplete = True
        flags.append("DCF_INPUT_INCOMPLETE")

    shares = features.market.get("diluted_shares").value if features.market.get("diluted_shares") else None
    if not is_finite(shares):
        shares = features.market.get("total_shares").value if features.market.get("total_shares") else None
        flags.append("SHARES_FALLBACK")
    if not is_finite(shares) or (shares is not None and shares <= 0):
        incomplete = True
        flags.append("SHARES_MISSING")

    price = features.market.get("close_unadjusted").value if features.market.get("close_unadjusted") else None
    net_debt = features.ttm.get("net_debt").value if features.ttm.get("net_debt") else None
    minority = None
    if features.annual:
        minority = features.annual[-1].fields.get("minority_interest")
    nwc0 = features.ttm.get("operating_nwc").value if features.ttm.get("operating_nwc") else None

    result_kwargs: dict[str, Any] = {
        "wacc": wacc_info["wacc"],
        "wacc_raw": wacc_info["wacc_raw"],
        "terminal_growth": wacc_info["terminal_growth"],
        "cost_of_equity": wacc_info["cost_of_equity"],
        "cost_of_debt": wacc_info["cost_of_debt"],
        "after_tax_cost_of_debt": wacc_info["after_tax_cost_of_debt"],
        "levered_beta": wacc_info["levered_beta"],
        "risk_free_rate": wacc_info["risk_free_rate"],
        "risk_free_rate_source": wacc_info["risk_free_rate_source"],
        "risk_free_rate_as_of": wacc_info["risk_free_rate_as_of"],
        "current_price": price,
        "equity_value_basis": "consolidated_fcff_minus_minority_interest",
        "assumptions": {**assumptions, "wacc": wacc_info, "normalized": normalized.inputs, "normalized_method": normalized.method},
        "flags": flags,
        "incomplete": incomplete,
        "normalized_fcff_0": normalized.normalized_fcff,
    }

    paths: dict[str, list[float] | None] = {}
    values: dict[str, float | None] = {}
    tv_weights: list[float] = []
    if not incomplete:
        inc_cap = float(config.require("valuation.incremental_roic_cap"))
        scenarios = {
            "bear": (assumptions["bear_revenue_growth"], assumptions["bear_ebit_margin"]),
            "base": (assumptions["base_revenue_growth"], assumptions["base_ebit_margin"]),
            "bull": (assumptions["bull_revenue_growth"], assumptions["bull_ebit_margin"]),
        }
        wacc, tg = wacc_info["wacc"], wacc_info["terminal_growth"]
        for name, (g, m) in scenarios.items():
            path = _project_path(revenue0, nwc0, g, m, tax, da_r, capex_r, nwc_r, years, inc_cap)  # type: ignore[arg-type]
            ev, pv_tv, weight = _present_value(path["fcff"], wacc, tg)
            if weight > float(config.require("valuation.terminal_value_dominant_threshold")):
                flags.append("TERMINAL_VALUE_DOMINANT")
            tv_weights.append(weight)
            paths[name] = path["fcff"]
            values[name] = _equity_per_share(ev, net_debt, minority, 0.0, shares)
        bear, base, bull = values.get("bear"), values.get("base"), values.get("bull")
        probs = config.require("valuation.scenario_probabilities")
        cweights = config.require("valuation.conservative_value_weights")
        expected = cons = None
        if all(is_finite(x) for x in (bear, base, bull)):
            expected = float(probs["bear"]) * bear + float(probs["base"]) * base + float(probs["bull"]) * bull  # type: ignore[operator]
            cons = float(cweights["bear"]) * bear + float(cweights["base"]) * base  # type: ignore[operator]
        mos = None
        if is_finite(cons) and is_finite(price) and cons > 0:  # type: ignore[operator]
            mos = 1.0 - price / cons  # type: ignore[operator]
        elif is_finite(cons) and cons is not None and cons <= 0:
            flags.append("MOS_INVALID_NONPOSITIVE_CONSERVATIVE_VALUE")
            mos = None
        result_kwargs.update(
            {
                "bear_value_per_share": bear,
                "base_value_per_share": base,
                "bull_value_per_share": bull,
                "expected_value_per_share": expected,
                "conservative_value_per_share": cons,
                "margin_of_safety": mos,
                "terminal_value_weight": max(tv_weights) if tv_weights else None,
                "fcff_path": paths,
            }
        )
        # base FCF growth: CAGR of base FCFF year1..year5, all positive
        base_path = paths.get("base") or []
        base_g = None
        if base_path and all(is_finite(x) and x > 0 for x in base_path):
            g, st = cagr(base_path[0], base_path[-1], len(base_path) - 1 if len(base_path) > 1 else 1)
            base_g = g if st == MetricStatus.OK or st.value == "OK" else None
            # cagr returns MetricStatus
            from avalue_screen.domain.enums import MetricStatus as MS
            if st is MS.OK:
                base_g = g
            else:
                base_g = None
                flags.append("BASE_FCF_GROWTH_INVALID")
        else:
            flags.append("BASE_FCF_GROWTH_INVALID")
        result_kwargs["base_fcf_growth"] = base_g

        # Reverse DCF uses the same starting FCFF, WACC, terminal growth
        start_fcff = normalized.normalized_fcff
        if not is_finite(start_fcff) or (start_fcff is not None and start_fcff <= 0):
            result_kwargs["reverse_dcf_status"] = ReverseDcfStatus.UNAVAILABLE_NONPOSITIVE_FCF
            flags.append("REVERSE_DCF_UNAVAILABLE_NONPOSITIVE_FCF")
        else:
            mcap = features.market.get("market_cap").value if features.market.get("market_cap") else None
            rd = solve_reverse_dcf(
                fcff0=start_fcff,  # type: ignore[arg-type]
                wacc=wacc,
                terminal_growth=tg,
                years=years,
                market_cap=mcap,
                net_debt=net_debt or 0.0,
                minority_interest=minority or 0.0,
                eligible_non_operating=0.0,
                config=config,
            )
            result_kwargs["g_implied"] = rd.g_implied  # remains None when out of range; never clamped
            result_kwargs["reverse_dcf_status"] = rd.status
            g_min = float(config.require("valuation.reverse_dcf_g_min"))
            g_max = float(config.require("valuation.reverse_dcf_g_max"))
            if rd.status is ReverseDcfStatus.SOLVED and is_finite(base_g) and is_finite(rd.g_implied):
                result_kwargs["reverse_dcf_gap"] = base_g - rd.g_implied  # type: ignore[operator]
            elif rd.status is ReverseDcfStatus.BELOW_SEARCH_RANGE and is_finite(base_g):
                # Actual g_implied < g_min, so the pessimism gap is *at least* base_g - g_min.
                # Use the lower bound for scoring only; do not report g_min as the solved root.
                result_kwargs["reverse_dcf_gap"] = base_g - g_min  # type: ignore[operator]
                flags.append(rd.status.value)
                flags.append("REVERSE_DCF_GAP_AT_LEAST_SEARCH_FLOOR")
            elif rd.status is ReverseDcfStatus.ABOVE_SEARCH_RANGE and is_finite(base_g):
                # Actual g_implied > g_max, so the gap is *at most* base_g - g_max (and may be more optimistic).
                # Conservative: do not overstate market pessimism.
                result_kwargs["reverse_dcf_gap"] = base_g - g_max  # type: ignore[operator]
                flags.append(rd.status.value)
                flags.append("REVERSE_DCF_GAP_AT_MOST_SEARCH_CEILING")
            else:
                flags.append(rd.status.value)

    # Multiples
    multiples = compute_multiples(features, normalized, config, company)
    result_kwargs["normalized_pe"] = multiples.normalized_pe
    result_kwargs["ev_ebit"] = multiples.ev_ebit
    result_kwargs["fcf_yield"] = multiples.fcf_yield
    result_kwargs["ev_ebit_discount"] = multiples.ev_ebit_discount
    flags.extend(multiples.flags)

    confidence = "high"
    if "TERMINAL_VALUE_DOMINANT" in flags or "WACC_CLAMPED" in flags:
        confidence = "reduced"
    if incomplete or "DCF_INPUT_INCOMPLETE" in flags:
        confidence = "low"
    result_kwargs["confidence"] = confidence
    result_kwargs["flags"] = sorted(set(flags))
    result_kwargs["incomplete"] = incomplete
    return ValuationResult(**result_kwargs)
