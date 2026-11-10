"""Hard gates: REJECT, REVIEW_REQUIRED, INSUFFICIENT_DATA. Scores never override these."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import AuditOpinion, EligibilityStatus
from avalue_screen.domain.models import CompanyInput, RuleHit
from avalue_screen.features.engine import FeatureSet
from avalue_screen.quality.data_quality import DataQualityReport
from avalue_screen.screening.universe import UniverseDecision
from avalue_screen.utils import is_finite


@dataclass
class GateResult:
    status: EligibilityStatus
    rejection: list[RuleHit] = field(default_factory=list)
    review: list[RuleHit] = field(default_factory=list)
    insufficient: list[RuleHit] = field(default_factory=list)

    @property
    def all_hits(self) -> list[RuleHit]:
        return list(self.rejection) + list(self.review) + list(self.insufficient)


def apply_hard_gates(
    company: CompanyInput,
    features: FeatureSet,
    dq: DataQualityReport,
    universe: UniverseDecision,
    as_of: date,
    config: ScreenConfig,
    *,
    pit_ok_for_history: bool = True,
    mode_requires_pit: bool = False,
) -> GateResult:
    rejection: list[RuleHit] = []
    review: list[RuleHit] = []
    insufficient: list[RuleHit] = []
    gates = config.section("quality_gates")

    # ----- REJECT (G-001..G-006) -----
    name = f"{company.company_name or ''} {company.symbol}"
    if (
        company.market.risk_warning_flag
        or company.market.delisting_risk_flag
        or "ST" in (company.company_name or "").upper()
        or "*ST" in name.upper()
    ):
        rejection.append(RuleHit(rule_id="G-001", severity=EligibilityStatus.REJECT, reason="ST / *ST / delisting-arrangement or confirmed delisting risk at the as-of date", evidence={"risk_warning_flag": company.market.risk_warning_flag, "delisting_risk_flag": company.market.delisting_risk_flag}))

    # G-002: adverse / disclaimer in last 3 FY, not subsequently cleared
    recent_audits = [
        a for a in company.audit_opinions
        if a.report_period <= as_of and (a.available_date is None or a.available_date <= as_of)
    ]
    last3_years = {d.fiscal_year for d in features.annual[-3:]} if features.annual else set()
    adverse = [
        a for a in recent_audits
        if a.opinion in {AuditOpinion.ADVERSE, AuditOpinion.DISCLAIMER}
        and (not last3_years or a.report_period.year in last3_years or a.report_period.year >= (as_of.year - 3))
        and not a.subsequently_cleared
    ]
    if adverse:
        rejection.append(RuleHit(rule_id="G-002", severity=EligibilityStatus.REJECT, reason="Adverse or disclaimer audit opinion in the last 3 fiscal years, not subsequently cleared", evidence={"periods": [a.report_period.isoformat() for a in adverse]}))

    # G-003 officially confirmed fraud / capital occupation / fraudulent issuance, not remediated
    frauds = [
        g for g in company.governance_events
        if g.officially_confirmed
        and g.event_type.lower() in {"fraud", "capital_occupation", "fraudulent_issuance", "financial_fraud"}
        and not g.remediated
        and (g.available_date or g.event_date) <= as_of
    ]
    if frauds:
        rejection.append(RuleHit(rule_id="G-003", severity=EligibilityStatus.REJECT, reason="Officially confirmed financial fraud, capital occupation or fraudulent issuance without credible remediation", evidence={"events": [g.event_type for g in frauds]}))

    # G-004 negative parent equity / persistent insolvency
    eq = features.ttm.get("equity_parent") if features.ttm else None
    eq_val = eq.value if eq else (features.annual[-1].metrics.get("equity_parent").value if features.annual and features.annual[-1].metrics.get("equity_parent") else None)
    if is_finite(eq_val) and eq_val < 0:  # type: ignore[operator]
        rejection.append(RuleHit(rule_id="G-004", severity=EligibilityStatus.REJECT, reason="Parent equity is negative (insolvency / going-concern risk)", evidence={"equity_parent": eq_val}))
    else:
        persist = int(gates.get("persistent_negative_equity_years", 2))
        recent_eq = []
        for ym in features.annual[-persist:]:
            mv = ym.metrics.get("equity_parent")
            recent_eq.append(mv.value if mv else ym.fields.get("equity_parent"))
        if recent_eq and all(is_finite(v) and v < 0 for v in recent_eq) and len(recent_eq) >= persist:
            rejection.append(RuleHit(rule_id="G-004", severity=EligibilityStatus.REJECT, reason="Persistent negative parent equity", evidence={"equity_parent_recent": recent_eq}))

    if company.market.listing_suspended_or_terminated or company.market.delisting_in_process:
        rejection.append(RuleHit(rule_id="G-005", severity=EligibilityStatus.REJECT, reason="Listing is suspended/terminated or the name is already in a delisting process", evidence={}))

    material_aq = [i for i in dq.accounting_issues if i.material]
    if material_aq:
        rejection.append(RuleHit(rule_id="G-006", severity=EligibilityStatus.REJECT, reason="Material accounting-identity or data-consistency failure that cannot be repaired", evidence={"checks": [i.check_id for i in material_aq]}))

    # ----- INSUFFICIENT_DATA (5.3) -----
    min_obs = int(config.require("screening.observation_history_years"))
    if dq.history_years < min_obs:
        insufficient.append(RuleHit(rule_id="I-001", severity=EligibilityStatus.INSUFFICIENT_DATA, reason=f"Fewer than {min_obs} comparable fiscal years", evidence={"history_years": dq.history_years}))
    if not dq.ttm_complete:
        insufficient.append(RuleHit(rule_id="I-002", severity=EligibilityStatus.INSUFFICIENT_DATA, reason="TTM key flow fields cannot be built from four consecutive single-quarter values", evidence={"missing": dq.missing_fields}))
    roic = features.ttm.get("ROIC") if features.ttm else None
    ebit = features.ttm.get("EBIT_adjusted") if features.ttm else None
    ic = features.ttm.get("invested_capital") if features.ttm else None
    if (ebit is None or not ebit.is_valid) or (ic is None or ic.value is None) or (roic is None or roic.status.value in {"MISSING"}):
        # Only if we cannot derive them
        if ebit is None or ebit.status.value == "MISSING" or ic is None or ic.status.value == "MISSING":
            insufficient.append(RuleHit(rule_id="I-003", severity=EligibilityStatus.INSUFFICIENT_DATA, reason="ROIC inputs (EBIT, invested capital or depreciation/amortization) are missing and cannot be derived reliably", evidence={}))
    # Cash-flow / BS coverage
    if "cash_flow_operating" in dq.missing_fields or "total_assets" in dq.missing_fields:
        insufficient.append(RuleHit(rule_id="I-004", severity=EligibilityStatus.INSUFFICIENT_DATA, reason="Key cash-flow or balance-sheet fields have insufficient coverage", evidence={"missing": dq.missing_fields}))
    mcap = features.market.get("market_cap")
    if mcap is None or not mcap.is_valid:
        insufficient.append(RuleHit(rule_id="I-005", severity=EligibilityStatus.INSUFFICIENT_DATA, reason="Price, shares or adjustment data cannot be aligned with corporate actions", evidence={}))
    if mode_requires_pit and not pit_ok_for_history:
        insufficient.append(RuleHit(rule_id="I-006", severity=EligibilityStatus.INSUFFICIENT_DATA, reason="Historical PIT mode is missing announcement dates or historical versions", evidence={}))
    # Currency/unit already errors at ingest if unconfirmed

    if dq.grade.value == "F" and not insufficient:
        insufficient.append(RuleHit(rule_id="I-007", severity=EligibilityStatus.INSUFFICIENT_DATA, reason="Data-quality grade F (core fields missing, material identity failure, or PIT failure)", evidence={"reasons": dq.reasons}))

    # ----- REVIEW_REQUIRED (R-001..R-012) -----
    w = features.windows
    adj_sum = w.get("y5_net_income_ex_nonrecurring_parent_sum")
    if is_finite(adj_sum) and adj_sum <= float(gates.get("min_5y_adjusted_net_income_sum", 0)):
        review.append(RuleHit(rule_id="R-001", severity=EligibilityStatus.REVIEW_REQUIRED, reason="Five-year cumulative adjusted parent net income is <= 0", evidence={"sum": adj_sum}))

    cfo_sum = w.get("y5_cash_flow_operating_sum")
    if is_finite(cfo_sum) and is_finite(adj_sum) and cfo_sum < 0 and adj_sum > 0:
        review.append(RuleHit(rule_id="R-002", severity=EligibilityStatus.REVIEW_REQUIRED, reason="Five-year cumulative CFO is negative while cumulative adjusted parent net income is positive", evidence={"cfo_sum": cfo_sum, "adj_np_sum": adj_sum}))

    conv = w.get("cash_conversion_5y")
    if conv is not None and conv.is_valid and conv.value is not None and conv.value < float(gates.get("min_cash_conversion_review", 0.5)):
        review.append(RuleHit(rule_id="R-003", severity=EligibilityStatus.REVIEW_REQUIRED, reason="Five-year cash conversion (CFO / net income) is below 0.5", evidence={"cash_conversion_5y": conv.value}))

    nd_ebitda = features.ttm.get("net_debt_to_ebitda") if features.ttm else None
    ebitda = features.ttm.get("EBITDA") if features.ttm else None
    nd = features.ttm.get("net_debt") if features.ttm else None
    hard_lev = float(gates.get("hard_net_debt_to_ebitda_review", 4.0))
    if nd_ebitda is not None and nd_ebitda.status.value == "NA_UNINTERPRETABLE_RATIO":
        review.append(RuleHit(rule_id="R-004", severity=EligibilityStatus.REVIEW_REQUIRED, reason="EBITDA is non-positive while net debt is positive", evidence={}))
    elif nd_ebitda is not None and nd_ebitda.is_valid and nd_ebitda.value is not None and nd_ebitda.status.value != "NET_CASH" and nd_ebitda.value > hard_lev:
        review.append(RuleHit(rule_id="R-004", severity=EligibilityStatus.REVIEW_REQUIRED, reason=f"Net debt / EBITDA is above {hard_lev}", evidence={"net_debt_to_ebitda": nd_ebitda.value}))

    icov = features.ttm.get("interest_coverage") if features.ttm else None
    min_ic = float(gates.get("min_interest_coverage_review", 1.5))
    if icov is not None and icov.is_valid and icov.value is not None and icov.value < min_ic:
        review.append(RuleHit(rule_id="R-005", severity=EligibilityStatus.REVIEW_REQUIRED, reason=f"TTM interest coverage is below {min_ic}", evidence={"interest_coverage": icov.value}))
    # consecutive two years below 1.5
    ic_series = w.get("y5_interest_coverage_values") or []
    years = w.get("annual_years") or []
    if len(ic_series) >= 2:
        last2 = ic_series[-2:]
        if all(is_finite(v) and v < min_ic for v in last2):
            if not any(h.rule_id == "R-005" for h in review):
                review.append(RuleHit(rule_id="R-005", severity=EligibilityStatus.REVIEW_REQUIRED, reason="Interest coverage was below 1.5 in each of the last two years", evidence={"last2": last2}))

    recv_gaps = w.get("recv_growth_gap_years") or []
    inv_gaps = w.get("inv_growth_gap_years") or []
    if _two_consecutive_exceed(recv_gaps) or _two_consecutive_exceed(inv_gaps):
        review.append(RuleHit(rule_id="R-006", severity=EligibilityStatus.REVIEW_REQUIRED, reason="Receivables or inventory grew more than 20 percentage points faster than revenue in two consecutive years", evidence={"receivables": recv_gaps, "inventory": inv_gaps}))

    gw = None
    if features.ttm and features.ttm.get("goodwill"):
        # goodwill is a raw field; may be only on annual
        pass
    if features.annual:
        last = features.annual[-1]
        gw = last.fields.get("goodwill")
        eqp = last.fields.get("equity_parent") or (last.metrics.get("equity_parent").value if last.metrics.get("equity_parent") else None)
        th = float(gates.get("goodwill_to_equity_review", 0.30))
        if is_finite(gw) and is_finite(eqp) and eqp not in (0, None) and eqp > 0 and gw / eqp > th:  # type: ignore[operator]
            review.append(RuleHit(rule_id="R-007", severity=EligibilityStatus.REVIEW_REQUIRED, reason="Goodwill / parent equity exceeds 30%", evidence={"goodwill": gw, "equity_parent": eqp, "ratio": gw / eqp}))

    # R-008 qualified / emphasis last 3 years affecting key items
    watch_audits = [
        a for a in recent_audits
        if a.opinion in {AuditOpinion.QUALIFIED, AuditOpinion.EMPHASIS}
        and a.affects_revenue_assets_going_concern_or_cash
        and (a.report_period.year >= as_of.year - 3)
    ]
    if watch_audits:
        review.append(RuleHit(rule_id="R-008", severity=EligibilityStatus.REVIEW_REQUIRED, reason="Qualified opinion or material emphasis of matter in the last 3 years affecting revenue, assets, going concern or cash flow", evidence={"periods": [a.report_period.isoformat() for a in watch_audits]}))

    # R-009 material litigation / customer loss / tech substitution / penalties / going concern
    event_types = {"litigation", "major_customer_loss", "technology_substitution", "safety_environment_penalty", "going_concern", "penalty"}
    evs = [
        g for g in company.governance_events
        if g.event_type.lower() in event_types
        and (g.available_date or g.event_date) <= as_of
        and g.event_date.year >= as_of.year - 3
    ]
    if evs:
        review.append(RuleHit(rule_id="R-009", severity=EligibilityStatus.REVIEW_REQUIRED, reason="Material litigation, customer loss, technology substitution, safety/environmental penalty or going-concern uncertainty in the last 3 years", evidence={"events": [g.event_type for g in evs]}))

    dil = features.market.get("dilution_3y")
    dil_th = float(gates.get("dilution_3y_review", 0.20))
    if dil and is_finite(dil.value) and dil.value > dil_th:
        # Capital raise did not improve per-share earnings / cash flow
        eps_cagr = w.get("adj_np_cagr_3y")
        # Use EPS if present in series; else adj NP vs share growth
        eps_improved = False
        # Conservative: if dilution > 20% and adj NP CAGR is missing or <= share growth, flag
        if eps_cagr is not None and eps_cagr.is_valid and eps_cagr.value is not None and eps_cagr.value > dil.value:
            eps_improved = True
        if not eps_improved:
            review.append(RuleHit(rule_id="R-010", severity=EligibilityStatus.REVIEW_REQUIRED, reason="Cumulative share-count dilution over 3 years exceeds 20% without an accompanying improvement in per-share earnings or cash flow", evidence={"dilution_3y": dil.value}))

    decline = w.get("roic_3y_decline") or {}
    dec = decline.get("last3_decline")
    roic_th = float(gates.get("roic_3y_decline_review", 0.05))
    # "过去5年ROIC持续下降且最近3年下降幅度超过5个百分点"
    if decline.get("continuous_decline") and is_finite(dec) and dec > roic_th:
        review.append(RuleHit(rule_id="R-011", severity=EligibilityStatus.REVIEW_REQUIRED, reason="ROIC declined continuously over 5 years and by more than 5 percentage points over the last 3 years", evidence=decline))

    ttm_adj = features.ttm.get("net_income_ex_nonrecurring_parent") if features.ttm else None
    ttm_fcf = features.ttm.get("FCF") if features.ttm else None
    if (ttm_adj and is_finite(ttm_adj.value) and ttm_adj.value < 0) or (ttm_fcf and is_finite(ttm_fcf.value) and ttm_fcf.value < 0):
        review.append(RuleHit(rule_id="R-012", severity=EligibilityStatus.REVIEW_REQUIRED, reason="Current TTM adjusted net income or FCF is negative (temporary shock vs lasting damage must be reviewed)", evidence={"ttm_adj_np": ttm_adj.value if ttm_adj else None, "ttm_fcf": ttm_fcf.value if ttm_fcf else None}))

    # Status priority: REJECT > INSUFFICIENT_DATA > REVIEW_REQUIRED > PASS
    if rejection:
        status = EligibilityStatus.REJECT
    elif insufficient:
        status = EligibilityStatus.INSUFFICIENT_DATA
    elif review:
        status = EligibilityStatus.REVIEW_REQUIRED
    else:
        status = EligibilityStatus.PASS

    return GateResult(status=status, rejection=rejection, review=review, insufficient=insufficient)


def _two_consecutive_exceed(rows: list[dict]) -> bool:
    if len(rows) < 2:
        return False
    flags = [bool(r.get("exceeds")) for r in rows]
    return any(a and b for a, b in zip(flags, flags[1:]))
