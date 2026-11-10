"""Data-quality grade and completeness checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import DataQualityGrade, PitStatus
from avalue_screen.domain.models import CompanyInput, FinancialStatement
from avalue_screen.features.engine import FeatureSet
from avalue_screen.pit.period_builder import PeriodPanel
from avalue_screen.quality.accounting_checks import AccountingIssue, run_accounting_checks
from avalue_screen.quality.outlier_checks import OutlierFlag, run_outlier_checks
from avalue_screen.utils import is_finite


CORE_FLOW = ("revenue", "net_income", "net_income_parent", "cash_flow_operating", "cash_paid_for_capex_ppe")
CORE_STOCK = ("total_assets", "total_liabilities", "total_equity", "equity_parent")
ROIC_NEEDED = ("operating_profit", "total_equity")


@dataclass
class DataQualityReport:
    grade: DataQualityGrade
    issues: list[dict[str, Any]] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    accounting_issues: list[AccountingIssue] = field(default_factory=list)
    outlier_flags: list[OutlierFlag] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    ttm_complete: bool = False
    history_years: int = 0
    sufficient_for_scoring: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade.value,
            "issues": self.issues,
            "missing_fields": self.missing_fields,
            "accounting_issues": [i.__dict__ for i in self.accounting_issues],
            "outlier_flags": [o.__dict__ for o in self.outlier_flags],
            "coverage": self.coverage,
            "ttm_complete": self.ttm_complete,
            "history_years": self.history_years,
            "sufficient_for_scoring": self.sufficient_for_scoring,
            "reasons": self.reasons,
        }


def assess_data_quality(
    company: CompanyInput,
    visible: list[FinancialStatement],
    panel: PeriodPanel,
    features: FeatureSet,
    config: ScreenConfig,
    pit_status: PitStatus,
    as_of: date,
) -> DataQualityReport:
    issues: list[dict[str, Any]] = []
    missing: list[str] = []
    reasons: list[str] = []

    # DQ-001 identity fields
    for stmt in visible:
        if not stmt.security_id or not stmt.report_period:
            issues.append({"check_id": "DQ-001", "message": "security_id or report_period missing", "period": str(stmt.report_period)})
        if stmt.announcement_datetime is None and stmt.available_date is None:
            issues.append({"check_id": "DQ-001", "message": "announcement/available date missing", "period": str(stmt.report_period)})
        if not stmt.version_id:
            issues.append({"check_id": "DQ-001", "message": "version_id missing", "period": str(stmt.report_period)})

    # DQ-002 coverage on annuals
    annuals = panel.annuals
    history_years = len(annuals)
    core = list(config.get("data_quality.coverage_core_fields") or list(CORE_FLOW) + list(CORE_STOCK))
    coverage: dict[str, float] = {}
    for field in core:
        present = sum(1 for a in annuals if is_finite(a.values.get(field)))
        coverage[field] = present / max(1, len(annuals))
        if annuals and present == 0:
            missing.append(field)

    # DQ-003 continuity: do not fill missing periods with 0
    if panel.conversion_issues:
        for msg in panel.conversion_issues:
            issues.append({"check_id": "DQ-003", "message": msg})

    # DQ-004 currency already normalized at ingest (would have raised)
    # DQ-005 period type vs period end
    for stmt in visible:
        expected_month = {1: 3, 2: 6, 3: 9, 4: 12}[stmt.statement_type.canonical().fiscal_quarter_end()]
        if stmt.report_period.month != expected_month:
            issues.append(
                {
                    "check_id": "DQ-005",
                    "message": f"report_period {stmt.report_period} does not match statement_type {stmt.statement_type}",
                    "period": stmt.report_period.isoformat(),
                }
            )

    # DQ-006 available_date should not precede announcement date
    for stmt in visible:
        if stmt.announcement_datetime is not None and stmt.available_date is not None:
            if stmt.available_date < stmt.announcement_datetime.date():
                issues.append(
                    {
                        "check_id": "DQ-006",
                        "message": "available_date is earlier than the announcement date",
                        "period": stmt.report_period.isoformat(),
                    }
                )
                reasons.append("PIT availability inconsistency")

    accounting = run_accounting_checks(visible, panel, config)
    outliers = run_outlier_checks(features, config)

    # TTM completeness for key flow fields
    ttm_complete = False
    if panel.ttm:
        latest = panel.ttm[-1]
        needed = ("revenue", "net_income_parent", "cash_flow_operating")
        ttm_complete = all(is_finite(latest.values.get(k)) for k in needed)
    if not ttm_complete:
        missing.append("TTM_KEY_FLOWS")
        reasons.append("TTM key flow fields cannot be built from four consecutive quarters")

    min_obs = int(config.require("screening.observation_history_years"))
    if history_years < min_obs:
        reasons.append(f"Fewer than {min_obs} comparable fiscal years")

    roic = features.ttm.get("ROIC") if features.ttm else None
    if roic is None or roic.status.value in {"MISSING", "INVALID", "NA_NONPOSITIVE_CAPITAL"}:
        # Check if inputs missing
        if not features.ttm or features.ttm.get("EBIT_adjusted") is None or features.ttm.get("invested_capital") is None:
            reasons.append("ROIC inputs (EBIT / invested capital) missing or invalid")
            if "EBIT_adjusted" not in missing:
                missing.append("EBIT_or_invested_capital")

    if not features.market.get("market_cap") or not features.market["market_cap"].is_valid:
        reasons.append("Market cap / price / shares cannot be aligned")
        missing.append("market_cap")

    material_aq = [i for i in accounting if i.material]
    pit_fail = any(i.get("check_id") == "DQ-006" for i in issues) and pit_status.value == "STRICT_PIT"

    # Grade
    estimated = any(
        (features.ttm.get(k) is not None and features.ttm[k].status.value == "ESTIMATED")
        for k in ("EBIT_adjusted", "NOPAT", "ROIC")
        if features.ttm
    )
    core_missing = any(coverage.get(f, 1) == 0 for f in core)
    if (
        history_years < min_obs
        or not ttm_complete
        or core_missing
        or material_aq
        or pit_fail
        or "market_cap" in missing
    ):
        grade = DataQualityGrade.F
        sufficient = False
    elif estimated or (pit_status.value != "STRICT_PIT") or (not ttm_complete):
        grade = DataQualityGrade.C
        sufficient = history_years >= min_obs and ttm_complete
        if pit_status.value != "STRICT_PIT":
            reasons.append("Non-PIT or incomplete restatement versions; observation only, no A grade")
    elif missing or outliers:
        grade = DataQualityGrade.B
        sufficient = True
    else:
        grade = DataQualityGrade.A
        sufficient = True

    # C/F cannot be A-grade later; C is observation.
    if grade is DataQualityGrade.F:
        sufficient = False

    return DataQualityReport(
        grade=grade,
        issues=issues,
        missing_fields=sorted(set(missing)),
        accounting_issues=accounting,
        outlier_flags=outliers,
        coverage=coverage,
        ttm_complete=ttm_complete,
        history_years=history_years,
        sufficient_for_scoring=sufficient and grade is not DataQualityGrade.F,
        reasons=reasons,
    )
