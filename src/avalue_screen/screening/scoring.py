"""100-point scoring. Each item returns score + rationale + source_metrics + rule_version.

Missing inputs stay missing; they are never treated as zero and then ranked.
Hard rejects / insufficient-data still allow provisional item scores for explainability,
but official A/B/C grades are withheld when required.
"""

from __future__ import annotations

from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import AuditOpinion, EligibilityStatus, Grade, MetricStatus
from avalue_screen.domain.models import CompanyInput, DimensionScore, ScoreItem, ValuationResult
from avalue_screen.features.engine import FeatureSet
from avalue_screen.quality.data_quality import DataQualityReport
from avalue_screen.screening.hard_gates import GateResult
from avalue_screen.screening.universe import UniverseDecision
from avalue_screen.utils import is_finite, ols_slope, score_from_bands, stdev


def _item(
    item_id: str,
    score: int | None,
    max_score: int,
    rationale: str,
    source: dict[str, Any],
    rule_version: str,
    *,
    missing: bool = False,
    review: bool = False,
    state: str | None = None,
) -> ScoreItem:
    return ScoreItem(
        item_id=item_id,
        score=None if missing else score,
        max_score=max_score,
        missing=missing,
        review=review,
        rationale=rationale,
        source_metrics=source,
        rule_version=rule_version,
        state=state,
    )


def _dimension(name: str, items: list[ScoreItem], max_score: int, config: ScreenConfig) -> DimensionScore:
    missing_points = sum(i.max_score for i in items if i.missing)
    ratio = missing_points / max_score if max_score else 0.0
    incomplete = ratio > float(config.require("data_quality.dimension_incomplete_missing_ratio"))
    available = [i.score for i in items if not i.missing and i.score is not None]
    score = sum(available) if (available or not items) else None
    if all(i.missing for i in items):
        score = None
        incomplete = True
    return DimensionScore(
        dimension=name,
        score=score,
        max_score=max_score,
        missing_points=missing_points,
        incomplete=incomplete,
        items=items,
        rule_version=config.rule_version,
    )


def score_company(
    company: CompanyInput,
    features: FeatureSet,
    valuation: ValuationResult | None,
    mispricing_view: dict[str, Any],
    gates: GateResult,
    dq: DataQualityReport,
    universe: UniverseDecision,
    config: ScreenConfig,
) -> dict[str, Any]:
    rule_v = config.rule_version
    commercial = _score_commercial(features, config, rule_v)
    health = _score_health(company, features, gates, config, rule_v)
    earnings = _score_earnings(features, config, rule_v)
    valuation_s = _score_valuation(features, valuation, config, rule_v)
    mispricing_s = _score_mispricing(features, valuation, mispricing_view, config, rule_v)

    dims = {
        "commercial_quality": commercial,
        "financial_health": health,
        "earnings_quality": earnings,
        "valuation_attractiveness": valuation_s,
        "mispricing_evidence": mispricing_s,
    }
    any_incomplete = any(d.incomplete for d in dims.values())
    total = 0
    total_known = True
    for d in dims.values():
        if d.score is None:
            total_known = False
        else:
            total += d.score
    total_score = total if total_known or any(d.score is not None for d in dims.values()) else None

    grade = _assign_grade(
        total_score,
        dims,
        gates,
        dq,
        universe,
        valuation,
        config,
        any_incomplete=any_incomplete,
    )
    return {
        "commercial_quality": commercial.score,
        "financial_health": health.score,
        "earnings_quality": earnings.score,
        "valuation_attractiveness": valuation_s.score,
        "mispricing_evidence": mispricing_s.score,
        "total": total_score,
        "grade": grade.value,
        "dimensions": {k: d.model_dump(mode="json") for k, d in dims.items()},
        "incomplete": any_incomplete,
        "listing_status": universe.listing_bucket,
    }


# ----- A. Commercial quality -----

def _score_commercial(features: FeatureSet, config: ScreenConfig, rule_v: str) -> DimensionScore:
    cfg = config.require("scoring.commercial_quality")
    w = features.windows
    items: list[ScoreItem] = []

    # 5y ROIC median
    roic_med = w.get("y5_ROIC_median")
    valid_roic_years = w.get("y5_ROIC_valid_years") or 0
    bands = cfg["roic_median"]["bands"]
    if not is_finite(roic_med) or valid_roic_years < 3:
        items.append(_item("A_roic_median", None, cfg["roic_median"]["points"], "5-year ROIC median is unavailable; not scored as zero", {"y5_ROIC_median": roic_med, "valid_years": valid_roic_years}, rule_v, missing=True))
    else:
        sc, band = score_from_bands(roic_med, bands)
        items.append(_item("A_roic_median", sc, cfg["roic_median"]["points"], f"5-year ROIC median is {roic_med:.2%}", {"y5_ROIC_median": roic_med, "band": band}, rule_v))

    # Persistence: years with valid positive ROIC
    pos = w.get("y5_ROIC_positive_years")
    if not is_finite(pos) or valid_roic_years == 0:
        items.append(_item("A_roic_persistence", None, cfg["roic_persistence"]["points"], "ROIC persistence cannot be assessed; not scored as zero", {"positive_years": pos}, rule_v, missing=True))
    else:
        exact = {int(k): int(v) for k, v in cfg["roic_persistence"]["exact"].items()}
        sc = exact.get(int(pos), cfg["roic_persistence"]["below"] if int(pos) < int(cfg["roic_persistence"]["min_years_for_below"]) else exact.get(int(pos), 0))
        if int(pos) not in exact:
            sc = int(cfg["roic_persistence"]["below"])
        items.append(_item("A_roic_persistence", int(sc), cfg["roic_persistence"]["points"], f"{int(pos)} of 5 years have valid positive ROIC", {"positive_years": pos, "valid_years": valid_roic_years}, rule_v))

    # Margin quality via structured state
    gm_slope = w.get("y5_gross_margin_slope")
    om_slope = w.get("y5_operating_margin_slope")
    gm_std = w.get("y5_gross_margin_std")
    om_std = w.get("y5_operating_margin_std")
    qcfg = config.section("qualitative")
    if not any(is_finite(x) for x in (gm_slope, om_slope)):
        items.append(_item("A_margin_quality", None, cfg["margin_quality"]["points"], "Margin trend cannot be assessed", {"gm_slope": gm_slope, "om_slope": om_slope}, rule_v, missing=True))
    else:
        slope = om_slope if is_finite(om_slope) else gm_slope
        vol = om_std if is_finite(om_std) else gm_std
        if slope >= float(qcfg["margin_slope_stable"]) and (vol is None or vol <= float(qcfg["margin_volatility_stable"])):
            state, sc = "stable_or_improving", int(cfg["margin_quality"]["stable_or_improving"])
        elif slope >= float(qcfg["margin_slope_mild_decline"]):
            state, sc = "slight_decline", int(cfg["margin_quality"]["slight_decline"])
        else:
            state, sc = "sustained_decline", int(cfg["margin_quality"]["sustained_decline"])
        items.append(_item("A_margin_quality", sc, cfg["margin_quality"]["points"], f"Margin quality state is {state} (EBIT-margin slope={slope})", {"gm_slope": gm_slope, "om_slope": om_slope, "gm_std": gm_std, "om_std": om_std}, rule_v, state=state))

    # Resilience
    rev_cagr = w.get("revenue_cagr_5y")
    np_cagr = w.get("adj_np_cagr_5y")
    rev_vals = w.get("y5_revenue_values") or []
    np_vals = w.get("y5_net_income_ex_nonrecurring_parent_values") or []
    if (rev_cagr is None or (not rev_cagr.is_valid and rev_cagr.status != MetricStatus.TURNAROUND)) and not any(is_finite(x) for x in rev_vals):
        items.append(_item("A_resilience", None, cfg["resilience"]["points"], "Revenue / earnings history is insufficient", {}, rule_v, missing=True))
    else:
        def _consecutive_decline(vals: list) -> int:
            best = cur = 0
            for a, b in zip(vals, vals[1:]):
                if is_finite(a) and is_finite(b) and b < a:
                    cur += 1
                    best = max(best, cur)
                else:
                    cur = 0
            return best
        cons = int(qcfg["resilience_consecutive_decline_years"])
        rev_down = _consecutive_decline(rev_vals) >= cons
        np_down = _consecutive_decline(np_vals) >= cons
        rev_growth = rev_cagr is not None and rev_cagr.is_valid and rev_cagr.value is not None and rev_cagr.value > 0
        np_growth = np_cagr is not None and np_cagr.is_valid and np_cagr.value is not None and np_cagr.value > 0
        if rev_growth and np_growth and not rev_down and not np_down:
            state, sc = "overall_growth", int(cfg["resilience"]["overall_growth"])
        elif (rev_down and np_down) or (not rev_growth and not np_growth and (rev_down or np_down)):
            state, sc = "sustained_decline", int(cfg["resilience"]["sustained_decline"])
        else:
            state, sc = "mixed", int(cfg["resilience"]["mixed"])
        items.append(_item(
            "A_resilience", sc, cfg["resilience"]["points"],
            f"Resilience state is {state}",
            {"revenue_cagr_5y": rev_cagr.value if rev_cagr else None, "adj_np_cagr_5y": np_cagr.value if np_cagr else None},
            rule_v, state=state,
        ))

    # Reinvestment efficiency
    fcf_sum = w.get("y5_FCF_sum")
    fcf_vals = w.get("y5_FCF_values") or []
    roic_std = w.get("y5_ROIC_std")
    roic_slope = w.get("y5_ROIC_slope")
    if not is_finite(fcf_sum):
        items.append(_item("A_reinvestment", None, cfg["reinvestment_efficiency"]["points"], "Five-year FCF is unavailable", {}, rule_v, missing=True))
    else:
        mostly_pos = sum(1 for v in fcf_vals if is_finite(v) and v > 0) >= max(1, len([v for v in fcf_vals if is_finite(v)]) - 1)
        roic_stable = (
            (roic_std is None or roic_std <= float(qcfg["roic_stability_std_max"]))
            and (roic_slope is None or roic_slope >= float(qcfg["roic_stable_slope_min"]))
        )
        roic_down = is_finite(roic_slope) and roic_slope < float(qcfg["roic_stable_slope_min"])
        if fcf_sum > 0 and mostly_pos and roic_stable:
            state, sc = "fcf_positive_roic_stable", int(cfg["reinvestment_efficiency"]["fcf_positive_roic_stable"])
        elif fcf_sum > 0:
            state, sc = "cyclical_cumulative_positive", int(cfg["reinvestment_efficiency"]["cyclical_cumulative_positive"])
        else:
            state, sc = "cash_burn_returns_down", int(cfg["reinvestment_efficiency"]["cash_burn_returns_down"])
            if not roic_down and fcf_sum <= 0:
                # Still long-term cash consumption
                state, sc = "cash_burn_returns_down", int(cfg["reinvestment_efficiency"]["cash_burn_returns_down"])
        items.append(_item(
            "A_reinvestment", sc, cfg["reinvestment_efficiency"]["points"],
            f"Reinvestment state is {state} (5y FCF sum={fcf_sum})",
            {"y5_FCF_sum": fcf_sum, "roic_std": roic_std, "roic_slope": roic_slope},
            rule_v, state=state,
        ))

    return _dimension("commercial_quality", items, int(config.require("scoring.weights.commercial_quality")), config)


# ----- B. Financial health -----

def _score_health(company: CompanyInput, features: FeatureSet, gates: GateResult, config: ScreenConfig, rule_v: str) -> DimensionScore:
    cfg = config.require("scoring.financial_health")
    items: list[ScoreItem] = []
    ttm = features.ttm

    nde = ttm.get("net_debt_to_ebitda")
    nd = ttm.get("net_debt")
    ebitda = ttm.get("EBITDA")
    pts = cfg["net_debt_to_ebitda"]["points"]
    if nde is None or nde.status == MetricStatus.MISSING:
        items.append(_item("B_leverage", None, pts, "Net debt / EBITDA is missing; not scored as zero or as low leverage", {}, rule_v, missing=True))
    elif nde.status == MetricStatus.NA_UNINTERPRETABLE_RATIO or (
        ebitda and is_finite(ebitda.value) and ebitda.value <= 0 and nd and is_finite(nd.value) and nd.value > 0
    ):
        items.append(_item("B_leverage", 0, pts, "EBITDA is non-positive while net debt is positive; leverage score is 0 and review is required", {"EBITDA": ebitda.value if ebitda else None, "net_debt": nd.value if nd else None}, rule_v, review=True, state="uninterpretable"))
    elif nde.status == MetricStatus.NET_CASH or (nd and is_finite(nd.value) and nd.value < 0):
        items.append(_item("B_leverage", 5 if 5 == int(cfg["net_debt_to_ebitda"].get("net_cash_or_le1", 5)) else int(cfg["net_debt_to_ebitda"]["net_cash_or_le1"]), pts, "Company is in a net-cash position", {"net_debt": nd.value if nd else None}, rule_v, state="net_cash"))
    elif nde.is_valid:
        sc, band = score_from_bands(nde.value, cfg["net_debt_to_ebitda"]["bands"])
        review = bool(band and band.get("review"))
        items.append(_item("B_leverage", sc, pts, f"Net debt / EBITDA is {nde.value:.2f}", {"net_debt_to_ebitda": nde.value}, rule_v, review=review))
    else:
        items.append(_item("B_leverage", None, pts, "Net debt / EBITDA is not usable", {"status": nde.status.value}, rule_v, missing=True))

    # Interest coverage
    icov = ttm.get("interest_coverage")
    pts = cfg["interest_coverage"]["points"]
    if icov is None or icov.status == MetricStatus.MISSING:
        items.append(_item("B_interest_coverage", None, pts, "Interest coverage is missing (interest field absent), not zero and not a perfect score", {}, rule_v, missing=True))
    elif icov.status == MetricStatus.NA_NO_INTEREST:
        # Do not auto-award full marks. Use net cash + completeness.
        if nd and is_finite(nd.value) and nd.value < 0 and ttm.get("unrestricted_cash") and ttm["unrestricted_cash"].is_valid:
            sc, state, why = 3, "no_interest_net_cash", "No material interest expense and a net-cash balance sheet; not a full mark because coverage is not observed"
        elif ttm.get("unrestricted_cash") and ttm["unrestricted_cash"].is_valid:
            sc, state, why = 2, "no_interest_observed", "Interest expense is near zero with complete cash data; coverage is NA_NO_INTEREST rather than infinite"
        else:
            sc, state, why = None, "no_interest_incomplete", "Interest is near zero but related coverage inputs are incomplete"
        items.append(_item("B_interest_coverage", sc, pts, why, {"status": "NA_NO_INTEREST"}, rule_v, missing=sc is None, state=state))
    elif icov.is_valid:
        sc, band = score_from_bands(icov.value, cfg["interest_coverage"]["bands"])
        items.append(_item("B_interest_coverage", sc, pts, f"Interest coverage is {icov.value:.2f}x", {"interest_coverage": icov.value}, rule_v, review=bool(band and band.get("review"))))
    else:
        items.append(_item("B_interest_coverage", None, pts, "Interest coverage is not usable", {"status": icov.status.value}, rule_v, missing=True))

    # Cash short-debt cover
    csd = ttm.get("cash_short_debt_cover")
    pts = cfg["cash_short_debt_cover"]["points"]
    if csd is None or csd.status == MetricStatus.MISSING:
        items.append(_item("B_cash_cover", None, pts, "Short-term debt cash coverage is missing", {}, rule_v, missing=True))
    elif csd.flags and "NO_SHORT_DEBT" in csd.flags:
        if nd and is_finite(nd.value) and nd.value < 0:
            items.append(_item("B_cash_cover", 3, pts, "No short-term interest-bearing debt and a net-cash position", {"net_debt": nd.value}, rule_v, state="no_short_debt_net_cash"))
        else:
            items.append(_item("B_cash_cover", 2, pts, "No short-term interest-bearing debt; coverage is not treated as infinite", {}, rule_v, state="no_short_debt"))
    elif csd.is_valid:
        sc, band = score_from_bands(csd.value, cfg["cash_short_debt_cover"]["bands"])
        items.append(_item("B_cash_cover", sc, pts, f"Cash / short-term interest-bearing debt is {csd.value:.2f}x", {"cash_short_debt_cover": csd.value}, rule_v))
    else:
        items.append(_item("B_cash_cover", None, pts, "Cash coverage is not usable", {"status": csd.status.value}, rule_v, missing=True))

    # Working capital quality
    pts = cfg["working_capital_quality"]["points"]
    recv_gaps = features.windows.get("recv_growth_gap_years") or []
    inv_gaps = features.windows.get("inv_growth_gap_years") or []
    rd = features.windows.get("y5_receivable_days_values") or []
    idays = features.windows.get("y5_inventory_days_values") or []
    if not recv_gaps and not inv_gaps and not any(is_finite(x) for x in rd + idays):
        items.append(_item("B_wc_quality", None, pts, "Working-capital trend cannot be assessed", {}, rule_v, missing=True))
    else:
        consec_ex = _consec_exceed(recv_gaps) or _consec_exceed(inv_gaps)
        any_ex = any(r.get("exceeds") for r in recv_gaps + inv_gaps)
        turn_change = 0.0
        if len(rd) >= 2 and is_finite(rd[0]) and is_finite(rd[-1]):
            turn_change = max(turn_change, abs(rd[-1] - rd[0]))  # type: ignore[operator]
        if len(idays) >= 2 and is_finite(idays[0]) and is_finite(idays[-1]):
            turn_change = max(turn_change, abs(idays[-1] - idays[0]))  # type: ignore[operator]
        stable_turn = turn_change <= float(config.require("qualitative.wc_turnover_stability_max_change"))
        if consec_ex:
            state, sc = "sustained_deterioration", int(cfg["working_capital_quality"]["sustained_deterioration"])
        elif any_ex or not stable_turn:
            state, sc = "slight_deterioration", int(cfg["working_capital_quality"]["slight_deterioration"])
        else:
            state, sc = "stable", int(cfg["working_capital_quality"]["stable"])
        items.append(_item("B_wc_quality", sc, pts, f"Working-capital quality state is {state}", {"recv_gaps": recv_gaps, "inv_gaps": inv_gaps}, rule_v, state=state))

    # Audit & governance
    pts = cfg["audit_governance"]["points"]
    audits = [a for a in company.audit_opinions]
    if any(a.opinion in {AuditOpinion.ADVERSE, AuditOpinion.DISCLAIMER} and not a.subsequently_cleared for a in audits) or any(h.rule_id in {"G-002", "G-003"} for h in gates.rejection):
        items.append(_item("B_audit", 0, pts, "Major unresolved audit or governance issue", {}, rule_v, review=True, state="major_unresolved"))
    elif any(a.opinion in {AuditOpinion.QUALIFIED, AuditOpinion.EMPHASIS} for a in audits) or any(h.rule_id in {"R-008", "R-009"} for h in gates.review):
        items.append(_item("B_audit", int(cfg["audit_governance"]["watch"]), pts, "Audit or governance items require follow-up", {}, rule_v, state="watch"))
    else:
        items.append(_item("B_audit", int(cfg["audit_governance"]["clean"]), pts, "No material audit or governance exception is recorded", {"audit_count": len(audits)}, rule_v, state="clean"))

    return _dimension("financial_health", items, int(config.require("scoring.weights.financial_health")), config)


def _consec_exceed(rows: list[dict]) -> bool:
    flags = [bool(r.get("exceeds")) for r in rows]
    return any(a and b for a, b in zip(flags, flags[1:]))


# ----- C. Earnings quality -----

def _score_earnings(features: FeatureSet, config: ScreenConfig, rule_v: str) -> DimensionScore:
    cfg = config.require("scoring.earnings_quality")
    w = features.windows
    items: list[ScoreItem] = []

    conv = w.get("cash_conversion_5y")
    pts = cfg["cash_conversion"]["points"]
    if conv is None or conv.status in {MetricStatus.MISSING}:
        items.append(_item("C_cash_conversion", None, pts, "5-year cash conversion is missing", {}, rule_v, missing=True))
    elif conv.status == MetricStatus.NA_UNINTERPRETABLE_RATIO:
        items.append(_item("C_cash_conversion", None, pts, "5-year cash conversion is uninterpretable (denominator not reliably positive); raw sums retained", conv.inputs, rule_v, missing=True))
    elif conv.is_valid:
        sc, band = score_from_bands(conv.value, cfg["cash_conversion"]["bands"])
        items.append(_item("C_cash_conversion", sc, pts, f"5-year CFO / net income is {conv.value:.2f}", {"cash_conversion_5y": conv.value}, rule_v, review=bool(band and band.get("review"))))
    else:
        items.append(_item("C_cash_conversion", None, pts, "Cash conversion is not usable", {"status": conv.status.value}, rule_v, missing=True))

    fconv = w.get("fcf_conversion_5y")
    pts = cfg["fcf_conversion"]["points"]
    if fconv is None or fconv.status in {MetricStatus.MISSING, MetricStatus.NA_UNINTERPRETABLE_RATIO}:
        items.append(_item("C_fcf_conversion", None, pts, "5-year FCF / adjusted net income is missing or uninterpretable", {}, rule_v, missing=True))
    elif fconv.is_valid:
        sc, band = score_from_bands(fconv.value, cfg["fcf_conversion"]["bands"])
        items.append(_item("C_fcf_conversion", sc, pts, f"5-year FCF / adjusted parent NI is {fconv.value:.2f}", {"fcf_conversion_5y": fconv.value}, rule_v))
    else:
        items.append(_item("C_fcf_conversion", None, pts, "FCF conversion is not usable", {}, rule_v, missing=True))

    rel = w.get("nonrecurring_reliance_5y")
    pts = cfg["nonrecurring_reliance"]["points"]
    if rel is None or rel.status in {MetricStatus.MISSING, MetricStatus.NA_UNINTERPRETABLE_RATIO}:
        # If both NI and nonrecurring are present as zero-ish, still missing/uninterpretable
        items.append(_item("C_nonrecurring", None, pts, "Non-recurring reliance cannot be interpreted (denominator missing, near zero, or sign-changing)", {}, rule_v, missing=True))
    elif rel.is_valid:
        # Use absolute reliance if nonrecurring can be negative
        value = abs(rel.value) if rel.value is not None else None
        sc, band = score_from_bands(value, cfg["nonrecurring_reliance"]["bands"])
        items.append(_item("C_nonrecurring", sc, pts, f"5-year non-recurring / parent NI is {rel.value:.2%}", {"nonrecurring_reliance_5y": rel.value}, rule_v))
    else:
        items.append(_item("C_nonrecurring", None, pts, "Non-recurring reliance is not usable", {}, rule_v, missing=True))

    pts = cfg["eps_share_change"]["points"]
    dil = features.market.get("dilution_3y")
    np_vals = w.get("y5_net_income_ex_nonrecurring_parent_values") or []
    cagr = w.get("adj_np_cagr_3y")
    dil_th = float(cfg["eps_share_change"]["significant_dilution_threshold"])
    if dil is None or dil.status == MetricStatus.MISSING:
        items.append(_item("C_eps_shares", None, pts, "Share-count history is missing; EPS/dilution item is not scored as zero", {}, rule_v, missing=True))
    else:
        dil_v = dil.value or 0.0
        # EPS path: last vs first of last 3 years, adjusted for shares if we can
        eps_up = cagr is not None and cagr.is_valid and cagr.value is not None and cagr.value > dil_v
        np_up = (
            (cagr is not None and cagr.is_valid and cagr.value is not None and cagr.value >= 0)
            or (len(np_vals) >= 2 and is_finite(np_vals[0]) and is_finite(np_vals[-1]) and np_vals[-1] >= np_vals[0])
        )
        if dil_v <= 0.02 and np_up:
            state, sc = "eps_up_shares_stable", int(cfg["eps_share_change"]["eps_up_shares_stable"])
        elif dil_v > dil_th and not eps_up:
            state, sc = "significant_dilution_eps_stagnant", int(cfg["eps_share_change"]["significant_dilution_eps_stagnant"])
        elif dil_v > 0.02 and eps_up:
            state, sc = "moderate_dilution_eps_up", int(cfg["eps_share_change"]["moderate_dilution_eps_up"])
        elif dil_v <= 0.02:
            state, sc = "eps_up_shares_stable", int(cfg["eps_share_change"]["eps_up_shares_stable"]) if np_up else int(cfg["eps_share_change"]["moderate_dilution_eps_up"])
            if not np_up:
                state = "moderate_dilution_eps_up"
        else:
            state, sc = "moderate_dilution_eps_up", int(cfg["eps_share_change"]["moderate_dilution_eps_up"])
        items.append(_item("C_eps_shares", sc, pts, f"Share/EPS state is {state} (3y dilution={dil_v:.2%})", {"dilution_3y": dil_v, "adj_np_cagr_3y": cagr.value if cagr else None}, rule_v, state=state))

    return _dimension("earnings_quality", items, int(config.require("scoring.weights.earnings_quality")), config)


# ----- D. Valuation -----

def _score_valuation(features: FeatureSet, valuation: ValuationResult | None, config: ScreenConfig, rule_v: str) -> DimensionScore:
    cfg = config.require("scoring.valuation_attractiveness")
    items: list[ScoreItem] = []

    def _neg_denom_block(name: str, pts: int, why: str) -> ScoreItem:
        return _item(name, 0, pts, why, {}, rule_v, state="negative_denominator")

    mos = valuation.margin_of_safety if valuation else None
    pts = cfg["margin_of_safety"]["points"]
    if valuation is None or valuation.incomplete or mos is None or valuation.conservative_value_per_share is None or valuation.conservative_value_per_share <= 0:
        items.append(_item("D_mos", None, pts, "Conservative intrinsic value / MOS is unavailable; PE is not used as a substitute", {"incomplete": valuation.incomplete if valuation else True}, rule_v, missing=True))
    else:
        sc, band = score_from_bands(mos, cfg["margin_of_safety"]["bands"])
        items.append(_item("D_mos", sc, pts, f"Margin of safety versus conservative value is {mos:.2%}", {"margin_of_safety": mos, "conservative_value_per_share": valuation.conservative_value_per_share, "current_price": valuation.current_price}, rule_v))

    pts = cfg["fcf_yield"]["points"]
    fy = valuation.fcf_yield if valuation else None
    # Negative normalized FCF must not produce a positive yield score
    if valuation is None or fy is None:
        # Distinguish missing vs negative
        if valuation and "NA_NEGATIVE_DENOMINATOR" in (valuation.flags or []) or (valuation and any("NEGATIVE" in f for f in valuation.flags)):
            items.append(_neg_denom_block("D_fcf_yield", pts, "Normalized FCF is non-positive; FCF yield does not receive a positive score"))
        else:
            items.append(_item("D_fcf_yield", None, pts, "Normalized FCF yield is unavailable", {}, rule_v, missing=True))
    elif fy < 0:
        items.append(_neg_denom_block("D_fcf_yield", pts, "Normalized FCF yield is negative; no positive valuation score"))
    else:
        sc, band = score_from_bands(fy, cfg["fcf_yield"]["bands"])
        items.append(_item("D_fcf_yield", sc, pts, f"Normalized FCF yield is {fy:.2%}", {"fcf_yield": fy}, rule_v))

    pts = cfg["ev_ebit_discount"]["points"]
    disc = valuation.ev_ebit_discount if valuation else None
    if valuation is None or disc is None:
        if valuation and any("NEGATIVE" in f or "NA_NEGATIVE_DENOMINATOR" in f for f in valuation.flags):
            items.append(_neg_denom_block("D_ev_ebit", pts, "Normalized EBIT is non-positive; EV/EBIT is not scored as cheap"))
        else:
            items.append(_item("D_ev_ebit", None, pts, "EV/EBIT discount versus a consistent reasonable multiple is unavailable; a peer set is not invented", {}, rule_v, missing=True))
    else:
        sc, band = score_from_bands(disc, cfg["ev_ebit_discount"]["bands"])
        items.append(_item("D_ev_ebit", sc, pts, f"EV/EBIT discount versus the reasonable multiple is {disc:.2%}", {"ev_ebit_discount": disc, "ev_ebit": valuation.ev_ebit}, rule_v))

    pts = cfg["reverse_dcf"]["points"]
    gap = valuation.reverse_dcf_gap if valuation else None
    if valuation is None or gap is None:
        items.append(_item("D_reverse_dcf", None, pts, "Reverse DCF gap is unavailable (non-positive FCF, no root, or sign-changing path)", {"status": valuation.reverse_dcf_status.value if valuation and valuation.reverse_dcf_status else None}, rule_v, missing=True))
    else:
        sc, band = score_from_bands(gap, cfg["reverse_dcf"]["bands"])
        items.append(_item("D_reverse_dcf", sc, pts, f"Base FCFF growth minus implied growth is {gap:.2%} (more positive = more pessimistic market path)", {"reverse_dcf_gap": gap, "g_implied": valuation.g_implied, "base_fcf_growth": valuation.base_fcf_growth, "status": valuation.reverse_dcf_status.value if valuation.reverse_dcf_status else None}, rule_v))

    return _dimension("valuation_attractiveness", items, int(config.require("scoring.weights.valuation_attractiveness")), config)


# ----- E. Mispricing evidence -----

def _score_mispricing(
    features: FeatureSet,
    valuation: ValuationResult | None,
    view: dict[str, Any],
    config: ScreenConfig,
    rule_v: str,
) -> DimensionScore:
    cfg = config.require("scoring.mispricing_evidence")
    mcfg = config.section("mispricing")
    items: list[ScoreItem] = []

    ret = features.market.get("price_12m_total_return")
    fund_decline = view.get("fundamental_decline")
    pts = cfg["price_fundamental_divergence"]["points"]
    if ret is None or not is_finite(ret.value) or fund_decline is None:
        if ret is None or not is_finite(ret.value):
            items.append(_item("E_divergence", None, pts, "12-month total return is unavailable; a price drop is not assumed", {}, rule_v, missing=True))
        else:
            # price known but fundamental change missing
            items.append(_item("E_divergence", None, pts, "Fundamental change versus price cannot be measured; a price drop alone is not mispricing evidence", {"price_12m_total_return": ret.value}, rule_v, missing=True))
    else:
        strong = float(mcfg["price_drawdown_strong_12m"])
        trig = float(mcfg["price_drawdown_trigger_12m"])
        mild = float(mcfg["price_drawdown_mild_12m"])
        max_fund = float(mcfg["max_fundamental_decline_for_strong_divergence"])
        r = ret.value
        if r <= strong and fund_decline <= max_fund:
            sc, state = int(cfg["price_fundamental_divergence"]["strong"]), "strong"
        elif trig < r <= 0 and False:
            sc, state = 0, "none"
        elif strong < r <= trig and fund_decline <= max_fund:
            sc, state = int(cfg["price_fundamental_divergence"]["moderate"]), "moderate"
        elif r <= mild and fund_decline <= float(mcfg.get("moderate_fundamental_decline", 0.20)):
            sc, state = int(cfg["price_fundamental_divergence"]["mild"]), "mild"
        else:
            sc, state = int(cfg["price_fundamental_divergence"]["none"]), "none"
        items.append(_item("E_divergence", sc, pts, f"Price/fundamental divergence state is {state} (12m return={r:.2%}, fundamental decline={fund_decline:.2%})", {"price_12m_total_return": r, "fundamental_decline": fund_decline}, rule_v, state=state))

    pts = cfg["shock_reversibility"]["points"]
    rev_state = view.get("reversibility_state") or "structural"
    mapping = {
        "confirmed_temporary": int(cfg["shock_reversibility"]["confirmed_temporary"]),
        "plausible_insufficient": int(cfg["shock_reversibility"]["plausible_insufficient"]),
        "structural": int(cfg["shock_reversibility"]["structural"]),
    }
    sc = mapping.get(rev_state, mapping["structural"])
    items.append(_item("E_reversibility", sc, pts, f"Shock reversibility state is {rev_state}", {"evidence_ids": view.get("evidence_ids") or []}, rule_v, state=rev_state))

    pts = cfg["implied_vs_evidence"]["points"]
    ev_state = view.get("implied_vs_evidence_state") or "none"
    mapping = {
        "strong": int(cfg["implied_vs_evidence"]["strong"]),
        "partial": int(cfg["implied_vs_evidence"]["partial"]),
        "none": int(cfg["implied_vs_evidence"]["none"]),
    }
    sc = mapping.get(ev_state, 0)
    items.append(_item("E_implied", sc, pts, f"Implied-vs-evidence state is {ev_state}", {"reverse_dcf_gap": valuation.reverse_dcf_gap if valuation else None}, rule_v, state=ev_state))

    pts = cfg["impairment_screen"]["points"]
    imp_state = view.get("impairment_state") or "uncertain"
    mapping = {
        "no_impairment": int(cfg["impairment_screen"]["no_impairment"]),
        "uncertain": int(cfg["impairment_screen"]["uncertain"]),
        "confirmed_impairment": int(cfg["impairment_screen"]["confirmed_impairment"]),
    }
    sc = mapping.get(imp_state, mapping["uncertain"])
    items.append(_item("E_impairment", sc, pts, f"Permanent-impairment screen state is {imp_state}", {"impairment_answers": view.get("impairment_answers") or {}}, rule_v, state=imp_state))

    return _dimension("mispricing_evidence", items, int(config.require("scoring.weights.mispricing_evidence")), config)


def _assign_grade(
    total: int | None,
    dims: dict[str, DimensionScore],
    gates: GateResult,
    dq: DataQualityReport,
    universe: UniverseDecision,
    valuation: ValuationResult | None,
    config: ScreenConfig,
    *,
    any_incomplete: bool,
) -> Grade:
    if gates.status is EligibilityStatus.INSUFFICIENT_DATA or dq.grade.value == "F" or any_incomplete:
        # Incomplete dimensions: no official A/B/C; data problem -> INSUFFICIENT_DATA if status already, else still withhold A/B/C
        if gates.status is EligibilityStatus.INSUFFICIENT_DATA or dq.grade.value == "F" or any_incomplete:
            if gates.status is EligibilityStatus.INSUFFICIENT_DATA or dq.grade.value == "F":
                return Grade.INSUFFICIENT_DATA
            # Dimension incomplete but other data exists: still no A/B/C. If there is an open review, REVIEW; else no official grade — use INSUFFICIENT_DATA when incompleteness is a data gap.
            if gates.status is EligibilityStatus.REVIEW_REQUIRED:
                return Grade.REVIEW
            return Grade.INSUFFICIENT_DATA
    if gates.status is EligibilityStatus.REJECT:
        # Rejected names do not receive A/B. They may still have a numeric score for explanation.
        if total is None:
            return Grade.D
        bands = config.require("scoring.grade_bands")
        if total >= int(bands["C"]["min"]):
            return Grade.D  # do not award C+ on a hard reject? SPEC says D is low priority 0-59, reject is separate eligibility. Grade can still be computed but A/B forbidden. I'll allow C/D only if they would otherwise qualify, else D.
        return Grade.D
    if gates.status is EligibilityStatus.REVIEW_REQUIRED:
        return Grade.REVIEW

    # PASS path
    if total is None:
        return Grade.INSUFFICIENT_DATA
    if dq.grade.value == "C":
        # observation only, no A
        letter = _letter_from_total(total, config)
        if letter in {Grade.A}:
            letter = Grade.B
        # still cannot be A; if B requirements fail, cap at C
        return letter if letter is not Grade.A else Grade.B

    letter = _letter_from_total(total, config)
    cq = dims["commercial_quality"].score
    fh = dims["financial_health"].score
    me = dims["mispricing_evidence"].score
    req_a = config.require("scoring.grade_A_requirements")
    req_b = config.require("scoring.grade_B_requirements")
    mos = valuation.margin_of_safety if valuation else None
    nde = None
    # financial leverage for A
    max_nde = float(req_a.get("max_net_debt_to_ebitda", 2.0))

    if letter is Grade.A:
        if (
            cq is not None and cq >= int(req_a["min_commercial_quality"])
            and fh is not None and fh >= int(req_a["min_financial_health"])
            and me is not None and me >= int(req_a["min_mispricing_evidence"])
            and is_finite(mos) and mos >= float(req_a["min_mos"])
            and dq.grade.value in {"A", "B"}
            and universe.listing_bucket == "FULL"
        ):
            return Grade.A
        # fall through to B check
        letter = Grade.B

    if letter is Grade.B:
        if (
            cq is not None and cq >= int(req_b["min_commercial_quality"])
            and fh is not None and fh >= int(req_b["min_financial_health"])
            and dq.grade.value in {"A", "B", "C"}
            and universe.listing_bucket in {"FULL", "PROVISIONAL"}
        ):
            # 3-5 year listings: max B (already B)
            if dq.grade.value == "C":
                return Grade.C
            return Grade.B
        letter = Grade.C if total >= int(config.require("scoring.grade_bands")["C"]["min"]) else Grade.D

    if letter is Grade.C and universe.listing_bucket == "TOO_NEW":
        return Grade.C  # still not a formal candidate
    return letter


def _letter_from_total(total: int, config: ScreenConfig) -> Grade:
    bands = config.require("scoring.grade_bands")
    # A: 85-100 inclusive
    if total >= int(bands["A"]["min"]):
        return Grade.A
    if total >= int(bands["B"]["min"]):
        return Grade.B
    if total >= int(bands["C"]["min"]):
        return Grade.C
    return Grade.D
