"""Core domain models. Values are stored in CNY yuan, shares, or decimal ratios."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from avalue_screen.domain.enums import (
    AuditOpinion,
    DataQualityGrade,
    EligibilityStatus,
    EvidenceGrade,
    EvidenceReviewStatus,
    Grade,
    ImpairmentAnswer,
    MetricStatus,
    MispricingCategory,
    PitStatus,
    ReverseDcfStatus,
    RunMode,
    StatementBlock,
    StatementType,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MutableModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class FieldValue(MutableModel):
    value: float | None = None
    unit: str = "yuan"
    currency: str = "CNY"
    source: str | None = None
    source_field: str | None = None
    imputation_flag: str | None = None
    quality_flag: str | None = None


class FinancialStatement(MutableModel):
    security_id: str
    symbol: str
    exchange: str | None = None
    report_period: date
    announcement_datetime: datetime | None = None
    available_date: date | None = None
    statement_type: StatementType
    version_id: str | None = None
    version_chain: str | None = None
    is_restatement: bool = False
    source: str | None = None
    currency: str = "CNY"
    income: dict[str, FieldValue] = Field(default_factory=dict)
    balance: dict[str, FieldValue] = Field(default_factory=dict)
    cashflow: dict[str, FieldValue] = Field(default_factory=dict)

    def field(self, name: str) -> FieldValue | None:
        for block in (self.income, self.balance, self.cashflow):
            if name in block:
                return block[name]
        return None

    def scalar(self, name: str) -> float | None:
        fv = self.field(name)
        if fv is None:
            return None
        return fv.value

    def all_fields(self) -> dict[str, FieldValue]:
        out: dict[str, FieldValue] = {}
        out.update(self.income)
        out.update(self.balance)
        out.update(self.cashflow)
        return out


class PriceBar(MutableModel):
    price_date: date
    close_unadjusted: float | None = None
    close_adjusted: float | None = None
    adjustment_method: str | None = None  # forward | backward
    total_return_index: float | None = None
    daily_turnover_value: float | None = None
    total_shares: float | None = None
    diluted_shares: float | None = None
    free_float_shares: float | None = None
    market_cap: float | None = None
    suspension_flag: bool = False


class ShareEvent(MutableModel):
    event_date: date
    announcement_datetime: datetime | None = None
    available_date: date | None = None
    event_type: str  # issuance | repurchase | stock_dividend | split | convert | other
    shares_delta: float | None = None
    shares_after: float | None = None
    source: str | None = None


class AuditRecord(MutableModel):
    report_period: date
    announcement_datetime: datetime | None = None
    available_date: date | None = None
    opinion: AuditOpinion = AuditOpinion.UNKNOWN
    emphasis_matters: bool = False
    affects_revenue_assets_going_concern_or_cash: bool = False
    subsequently_cleared: bool = False
    notes: str | None = None


class GovernanceEvent(MutableModel):
    event_date: date
    announcement_datetime: datetime | None = None
    available_date: date | None = None
    event_type: str  # fraud | capital_occupation | fraudulent_issuance | litigation | penalty | going_concern | other
    officially_confirmed: bool = False
    remediated: bool = False
    notes: str | None = None


class EvidenceRecord(MutableModel):
    evidence_id: str
    security_id: str
    category: MispricingCategory | str
    claim: str
    evidence_summary: str
    source_name: str
    source_url_or_doc_id: str | None = None
    published_at: date | None = None
    evidence_grade: EvidenceGrade = EvidenceGrade.C
    supports_reversibility: bool | None = None
    review_status: EvidenceReviewStatus = EvidenceReviewStatus.PENDING
    reviewer: str | None = None
    reviewed_at: date | None = None


class ImpairmentCheck(MutableModel):
    item_id: str
    question: str
    answer: ImpairmentAnswer = ImpairmentAnswer.UNKNOWN
    evidence_summary: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    material_to_long_term_earnings: bool | None = None


class MarketSnapshot(MutableModel):
    prices: list[PriceBar] = Field(default_factory=list)
    share_events: list[ShareEvent] = Field(default_factory=list)
    risk_warning_flag: bool = False
    delisting_risk_flag: bool = False
    delisting_in_process: bool = False
    listing_suspended_or_terminated: bool = False
    suspension_flag: bool = False
    listing_date: date | None = None
    delisting_date: date | None = None
    levered_beta: float | None = None
    beta_source: str | None = None
    industry: str | None = None
    sector: str | None = None
    is_financial: bool = False
    is_cyclical: bool | None = None
    reasonable_ev_ebit: float | None = None
    peer_ev_ebit_median: float | None = None


class RiskFreeRatePoint(MutableModel):
    rate_date: date
    yield_10y: float
    source: str | None = None


class IndexBar(MutableModel):
    price_date: date
    close: float
    symbol: str = "CSI300"


class CompanyInput(MutableModel):
    security_id: str
    symbol: str
    company_name: str | None = None
    exchange: str | None = None
    statements: list[FinancialStatement] = Field(default_factory=list)
    market: MarketSnapshot = Field(default_factory=MarketSnapshot)
    audit_opinions: list[AuditRecord] = Field(default_factory=list)
    governance_events: list[GovernanceEvent] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    impairment_checks: list[ImpairmentCheck] = Field(default_factory=list)
    # If True, the payload is known to contain only final-vintage numbers.
    final_vintage_only: bool = False
    historical_versions_available: bool | None = None
    currency: str = "CNY"
    notes: str | None = None


class UniversePayload(MutableModel):
    model_config = ConfigDict(extra="allow")
    as_of_date: date | None = None
    companies: list[CompanyInput] = Field(default_factory=list)
    risk_free_curve: list[RiskFreeRatePoint] = Field(default_factory=list)
    index_prices: list[IndexBar] = Field(default_factory=list)
    trading_days: list[date] = Field(default_factory=list)
    holidays: list[date] = Field(default_factory=list)
    # Dataset-level PIT metadata
    final_vintage_only: bool = False
    historical_versions_available: bool | None = None
    data_source: str | None = None
    data_version: str | None = None


class MetricValue(MutableModel):
    name: str
    value: float | None = None
    status: MetricStatus = MetricStatus.OK
    window: str | None = None
    formula_id: str | None = None
    formula_version: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    flags: list[str] = Field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.value is not None and self.status in {
            MetricStatus.OK,
            MetricStatus.ESTIMATED,
            MetricStatus.NET_CASH,
            MetricStatus.TAX_RATE_FALLBACK,
            MetricStatus.COST_OF_DEBT_FALLBACK,
            MetricStatus.WACC_CLAMPED,
            MetricStatus.TERMINAL_VALUE_DOMINANT,
            MetricStatus.BETA_FALLBACK,
            MetricStatus.SHARES_FALLBACK,
        }


class ScoreItem(MutableModel):
    item_id: str
    score: int | None = None
    max_score: int
    missing: bool = False
    review: bool = False
    rationale: str
    source_metrics: dict[str, Any] = Field(default_factory=dict)
    rule_version: str
    state: str | None = None


class DimensionScore(MutableModel):
    dimension: str
    score: int | None = None
    max_score: int
    missing_points: int = 0
    incomplete: bool = False
    items: list[ScoreItem] = Field(default_factory=list)
    rule_version: str


class RuleHit(MutableModel):
    rule_id: str
    severity: EligibilityStatus
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ValuationResult(MutableModel):
    bear_value_per_share: float | None = None
    base_value_per_share: float | None = None
    bull_value_per_share: float | None = None
    expected_value_per_share: float | None = None
    conservative_value_per_share: float | None = None
    current_price: float | None = None
    margin_of_safety: float | None = None
    wacc: float | None = None
    wacc_raw: float | None = None
    terminal_growth: float | None = None
    terminal_value_weight: float | None = None
    cost_of_equity: float | None = None
    cost_of_debt: float | None = None
    after_tax_cost_of_debt: float | None = None
    levered_beta: float | None = None
    risk_free_rate: float | None = None
    risk_free_rate_source: str | None = None
    risk_free_rate_as_of: date | None = None
    normalized_fcff_0: float | None = None
    base_fcf_growth: float | None = None
    g_implied: float | None = None
    reverse_dcf_status: ReverseDcfStatus | None = None
    reverse_dcf_gap: float | None = None
    normalized_pe: float | None = None
    ev_ebit: float | None = None
    fcf_yield: float | None = None
    ev_ebit_discount: float | None = None
    fcff_path: dict[str, list[float] | None] = Field(default_factory=dict)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    confidence: str | None = None  # high | reduced | low
    equity_value_basis: str = "consolidated_fcff_minus_minority_interest"
    incomplete: bool = False


class CompanyResult(MutableModel):
    security_id: str
    symbol: str
    company_name: str | None = None
    exchange: str | None = None
    as_of_date: date
    run_mode: RunMode
    pit_status: PitStatus
    data_quality_grade: DataQualityGrade
    eligibility_status: EligibilityStatus
    rejection_reasons: list[RuleHit] = Field(default_factory=list)
    review_flags: list[RuleHit] = Field(default_factory=list)
    insufficient_reasons: list[RuleHit] = Field(default_factory=list)
    scores: dict[str, Any] = Field(default_factory=dict)
    key_metrics: dict[str, Any] = Field(default_factory=dict)
    valuation: dict[str, Any] = Field(default_factory=dict)
    mispricing: dict[str, Any] = Field(default_factory=dict)
    explainability: dict[str, Any] = Field(default_factory=dict)
    listing_status: str | None = None  # FULL | PROVISIONAL | TOO_NEW
    industry: str | None = None
    sector: str | None = None
    liquidity_ok: bool | None = None
    daily_turnover_cny: float | None = None
    candidate_eligible: bool = False
    universe_included: bool = True
    universe_exclusion_reason: str | None = None

    def to_profile_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
