"""End-to-end screening pipeline. Pure with respect to inputs + config."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from avalue_screen.config import ScreenConfig, load_config
from avalue_screen.domain.enums import EligibilityStatus, FinancialDataMode, Grade, PitStatus, RunMode
from avalue_screen.domain.errors import InsufficientPitHistoryError
from avalue_screen.domain.models import CompanyInput, CompanyResult, UniversePayload, ValuationResult
from avalue_screen.features.engine import FeatureSet, compute_features
from avalue_screen.ingestion.field_mapper import FieldMapper
from avalue_screen.ingestion.loaders import load_input
from avalue_screen.mispricing.divergence import compute_divergence
from avalue_screen.mispricing.evidence import evaluate_evidence
from avalue_screen.mispricing.impairment_checklist import evaluate_impairment
from avalue_screen.pit.availability import (
    assess_pit_capability,
    fill_available_dates,
)
from avalue_screen.pit.period_builder import build_period_panel
from avalue_screen.pit.version_selector import select_visible_statements
from avalue_screen.quality.data_quality import DataQualityReport, assess_data_quality
from avalue_screen.reporting.csv_export import write_csv_reports
from avalue_screen.reporting.explainability import build_explainability
from avalue_screen.reporting.json_export import hash_input_tree, write_json_reports, write_run_manifest
from avalue_screen.screening.hard_gates import apply_hard_gates
from avalue_screen.screening.ranking import official_candidates, rank_results
from avalue_screen.screening.scoring import score_company
from avalue_screen.screening.universe import classify_security
from avalue_screen.utils import TradingCalendar, parse_date
from avalue_screen.valuation.dcf import run_dcf
from avalue_screen.valuation.normalized_earnings import normalize_earnings


@dataclass
class PipelineResult:
    as_of: date
    mode: RunMode
    pit_status: PitStatus
    results: list[CompanyResult]
    data_quality: dict[str, Any]
    pit_reasons: list[str]
    output_dir: Path | None = None


def _calendar(payload: UniversePayload) -> TradingCalendar:
    return TradingCalendar(payload.trading_days, payload.holidays)


def _financial_data_mode(mode: RunMode, explicit: str | None) -> FinancialDataMode:
    if explicit:
        return FinancialDataMode(explicit)
    if mode is RunMode.FINAL_VINTAGE_RESEARCH:
        return FinancialDataMode.FINAL_VINTAGE
    return FinancialDataMode.STRICT_PIT


def screen_universe(
    payload: UniversePayload,
    as_of: date,
    mode: RunMode,
    config: ScreenConfig,
    *,
    financial_data_mode: str | None = None,
) -> PipelineResult:
    calendar = _calendar(payload)
    # Fill available dates before PIT assessment so coverage is accurate.
    filled_companies: list[CompanyInput] = []
    for c in payload.companies:
        stmts = fill_available_dates(c.statements, calendar, config)
        filled_companies.append(c.model_copy(update={"statements": stmts}))
    payload = payload.model_copy(update={"companies": filled_companies})

    fd_mode = _financial_data_mode(mode, financial_data_mode)
    assessment = assess_pit_capability(payload, mode, financial_data_mode=fd_mode)
    pit_status = assessment.pit_status
    if mode is RunMode.FINAL_VINTAGE_RESEARCH:
        pit_status = PitStatus.NON_PIT_FINAL_VINTAGE

    results: list[CompanyResult] = []
    dq_bundle: dict[str, Any] = {
        "pit_status": pit_status.value,
        "pit_reasons": assessment.reasons,
        "announcement_coverage": assessment.announcement_coverage,
        "version_coverage": assessment.version_coverage,
        "companies": [],
    }

    for company in payload.companies:
        result, dq = screen_company(
            company,
            payload,
            as_of,
            mode,
            config,
            calendar=calendar,
            pit_status=pit_status,
            mode_requires_pit=(mode is RunMode.HISTORICAL_PIT_BACKTEST),
        )
        results.append(result)
        dq_bundle["companies"].append({"security_id": company.security_id, "symbol": company.symbol, **dq.to_dict()})

    results = rank_results(results)
    return PipelineResult(
        as_of=as_of,
        mode=mode,
        pit_status=pit_status,
        results=results,
        data_quality=dq_bundle,
        pit_reasons=assessment.reasons,
    )


def screen_company(
    company: CompanyInput,
    payload: UniversePayload,
    as_of: date,
    mode: RunMode,
    config: ScreenConfig,
    *,
    calendar: TradingCalendar,
    pit_status: PitStatus,
    mode_requires_pit: bool,
) -> tuple[CompanyResult, DataQualityReport]:
    # Liquidity / suspension from latest bar
    from avalue_screen.features.engine import _bar_on_or_before

    bar = _bar_on_or_before(company.market.prices, as_of)
    suspended = bool(company.market.suspension_flag or (bar.suspension_flag if bar else False))
    no_price = bar is None or (bar.close_unadjusted is None and bar.market_cap is None)
    if no_price and config.get("universe.exclude_suspended_on_as_of_date", True):
        suspended = True
    # Turnover computed later from features; preliminary None
    uni = classify_security(company, as_of, config, turnover=None, suspended_on_as_of=suspended)

    visible = select_visible_statements(company.statements, as_of)
    panel = build_period_panel(visible, config)
    features = compute_features(company, panel, as_of, config, calendar=calendar)

    # Reclassify liquidity now that features exist
    turnover = features.market.get("daily_turnover_cny").value if features.market.get("daily_turnover_cny") else None
    uni = classify_security(company, as_of, config, turnover=turnover, suspended_on_as_of=suspended)

    dq = assess_data_quality(company, visible, panel, features, config, pit_status, as_of)
    gates = apply_hard_gates(
        company,
        features,
        dq,
        uni,
        as_of,
        config,
        pit_ok_for_history=pit_status is PitStatus.STRICT_PIT,
        mode_requires_pit=mode_requires_pit,
    )

    # Valuation (still computed for explanation even if insufficient, but marked incomplete)
    normalized = normalize_earnings(company, features, config)
    valuation = run_dcf(company, features, normalized, config, as_of, payload.risk_free_curve)

    impairment = evaluate_impairment(company)
    divergence = compute_divergence(features, normalized, config)
    evidence_view = evaluate_evidence(
        company,
        as_of,
        config,
        reverse_dcf_gap=valuation.reverse_dcf_gap,
        divergence=divergence,
        impairment_state=impairment["impairment_state"],
        categories=[impairment["structural_category"]] if impairment["structural_category"] else None,
    )
    if impairment["review_required"] and not any(h.rule_id == "R-IMP" for h in gates.review):
        from avalue_screen.domain.models import RuleHit

        gates.review.append(
            RuleHit(
                rule_id="R-IMP",
                severity=EligibilityStatus.REVIEW_REQUIRED,
                reason="Permanent-impairment checklist has a material YES",
                evidence={"items": impairment["material_yes_items"]},
            )
        )
        if gates.status is EligibilityStatus.PASS:
            gates.status = EligibilityStatus.REVIEW_REQUIRED

    mispricing_view = {
        **divergence,
        **evidence_view,
        **impairment,
        "category": evidence_view.get("categories") or ([impairment["structural_category"]] if impairment["structural_category"] else []),
    }

    scores = score_company(company, features, valuation, mispricing_view, gates, dq, uni, config)
    explain = build_explainability(features, scores, dq, valuation, config)

    # Candidate eligibility: PASS, formal listing tenure, liquidity, not universe-excluded, grade not REVIEW/INSUFF
    liquidity_blocks = bool(config.get("universe.liquidity_blocks_candidate_list", True))
    liquidity_ok = uni.liquidity_ok
    listing_ok = uni.listing_bucket in {"FULL", "PROVISIONAL"}
    grade = scores.get("grade")
    min_grades = {str(x) for x in (config.get("screening.candidate_min_grades") or ["A", "B"])}
    candidate = (
        gates.status is EligibilityStatus.PASS
        and uni.included
        and listing_ok
        and grade not in {Grade.REVIEW.value, Grade.INSUFFICIENT_DATA.value, "REVIEW", "INSUFFICIENT_DATA"}
        and grade in min_grades
        and (liquidity_ok is not False if liquidity_blocks else True)
        and uni.listing_bucket != "TOO_NEW"
    )
    # 3-year observation listing can be provisional B at most, already handled in scoring; still candidate if PASS

    key_metrics = _key_metrics(features, valuation, normalized_method=normalized.method)

    result = CompanyResult(
        security_id=company.security_id,
        symbol=company.symbol,
        company_name=company.company_name,
        exchange=company.exchange,
        as_of_date=as_of,
        run_mode=mode,
        pit_status=pit_status,
        data_quality_grade=dq.grade,
        eligibility_status=gates.status if uni.included or gates.rejection else (
            EligibilityStatus.REJECT if not uni.included and (uni.is_st_or_risk or uni.is_financial or uni.is_suspended) else gates.status
        ),
        rejection_reasons=list(gates.rejection),
        review_flags=list(gates.review),
        insufficient_reasons=list(gates.insufficient),
        scores=scores,
        key_metrics=key_metrics,
        valuation=valuation.model_dump(mode="json"),
        mispricing={
            "category": mispricing_view.get("category") or mispricing_view.get("categories") or [],
            "price_12m_total_return": divergence.get("price_12m_total_return"),
            "fundamental_price_divergence": divergence.get("price_fundamental_divergence_score"),
            "fundamental_decline": divergence.get("fundamental_decline"),
            "permanent_impairment_checks": impairment.get("checks") or [],
            "evidence_refs": evidence_view.get("evidence_ids") or [],
            "reversibility_state": evidence_view.get("reversibility_state"),
            "implied_vs_evidence_state": evidence_view.get("implied_vs_evidence_state"),
            "impairment_state": impairment.get("impairment_state"),
            "research_queue": divergence.get("research_queue"),
        },
        explainability=explain,
        listing_status=uni.listing_bucket,
        industry=company.market.industry,
        sector=company.market.sector,
        liquidity_ok=liquidity_ok,
        daily_turnover_cny=turnover,
        candidate_eligible=candidate,
        universe_included=uni.included,
        universe_exclusion_reason=uni.reason,
    )
    # Universe exclusions that are ST / delist / financial / suspended: ensure they are not official candidates.
    if not uni.included:
        result.candidate_eligible = False
        # If excluded for ST/delist, and G-001 not already present, they may still have been rejected by G-001.
        # Financial / board filters are universe exclusions, not necessarily G-codes.
        if result.eligibility_status is EligibilityStatus.PASS and uni.reason:
            # Keep PASS internally only if it's a board/liquidity style filter? Liquidity is candidate flag only.
            # Financial / non-ordinary / board should not be official candidates; status can remain as computed
            # but they must not appear on the candidate list (already handled).
            pass
    return result, dq


def _key_metrics(features: FeatureSet, valuation: ValuationResult, *, normalized_method: str) -> dict[str, Any]:
    def v(name: str, store: dict | None = None) -> Any:
        store = store or features.ttm
        mv = store.get(name)
        if mv is None:
            return None
        if hasattr(mv, "value"):
            return {"value": mv.value, "status": mv.status.value, "formula_version": mv.formula_version, "inputs": mv.inputs, "flags": mv.flags}
        return mv

    w = features.windows
    return {
        "ttm_period": features.ttm_period.isoformat() if features.ttm_period else None,
        "ROIC_ttm": v("ROIC"),
        "ROIC_5y_median": w.get("y5_ROIC_median"),
        "ROE_ttm": v("ROE"),
        "gross_margin_ttm": v("gross_margin"),
        "operating_margin_ttm": v("operating_margin"),
        "net_debt_to_ebitda": v("net_debt_to_ebitda"),
        "interest_coverage": v("interest_coverage"),
        "FCF_ttm": v("FCF"),
        "cash_conversion_5y": v("cash_conversion_5y", w) if not isinstance(w.get("cash_conversion_5y"), dict) else None,
        "cash_conversion_5y_value": (w.get("cash_conversion_5y").value if w.get("cash_conversion_5y") is not None and hasattr(w.get("cash_conversion_5y"), "value") else None),
        "fcf_conversion_5y_value": (w.get("fcf_conversion_5y").value if w.get("fcf_conversion_5y") is not None and hasattr(w.get("fcf_conversion_5y"), "value") else None),
        "revenue_cagr_5y": (w.get("revenue_cagr_5y").value if w.get("revenue_cagr_5y") is not None and hasattr(w.get("revenue_cagr_5y"), "value") else None),
        "dilution_3y": v("dilution_3y", features.market),
        "price_12m_total_return": v("price_12m_total_return", features.market),
        "market_cap": v("market_cap", features.market),
        "normalized_method": normalized_method,
        "annual_years": w.get("annual_years"),
        "series": features.series,
    }


def run_from_paths(
    *,
    input_path: str | Path,
    output_dir: str | Path,
    as_of: date | str,
    mode: str | RunMode,
    config_path: str | Path | None = None,
    mapping_path: str | Path | None = None,
    financial_data_mode: str | None = None,
) -> PipelineResult:
    as_of_d = parse_date(as_of)
    if as_of_d is None:
        raise ValueError("as_of date is required")
    mode_e = mode if isinstance(mode, RunMode) else RunMode(mode)
    config = load_config(config_path)
    mapper = None
    if mapping_path:
        mapper = FieldMapper.from_path(mapping_path)
    else:
        default_map = Path(config.source_path).parent / "field_mapping.yaml" if config.source_path else None
        if default_map and default_map.exists():
            mapper = FieldMapper.from_path(default_map)
    payload = load_input(input_path, config, mapper)
    pipeline = screen_universe(payload, as_of_d, mode_e, config, financial_data_mode=financial_data_mode)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv_reports(pipeline.results, output_dir)
    write_json_reports(pipeline.results, output_dir, pipeline.data_quality)
    write_run_manifest(
        output_dir,
        config=config,
        as_of=as_of_d,
        mode=mode_e,
        pit_status=pipeline.pit_status,
        input_path=input_path,
        input_hashes=hash_input_tree(input_path),
        extra={"company_count": len(pipeline.results), "pit_reasons": pipeline.pit_reasons},
    )
    pipeline.output_dir = output_dir
    return pipeline
