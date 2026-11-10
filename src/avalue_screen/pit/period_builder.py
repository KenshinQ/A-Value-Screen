"""Convert YTD cumulative flow items into single-quarter values and build TTM series.

Period differences are only taken along the same version chain. If an annual restatement
is selected together with an earlier-quarter statement from a different chain, the
quarterly residual is marked invalid rather than mixing vintages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import StatementType
from avalue_screen.domain.models import FieldValue, FinancialStatement

# Flow (period) fields are differenced / TTM-summed. Stock (balance-sheet) fields are not.
DEFAULT_FLOW_FIELDS = {
    "revenue",
    "cost_of_revenue",
    "selling_expense",
    "admin_expense",
    "rd_expense",
    "finance_expense",
    "interest_expense_gross",
    "interest_income",
    "operating_profit",
    "non_operating_income_net",
    "profit_before_tax",
    "income_tax_expense",
    "net_income",
    "net_income_parent",
    "net_income_ex_nonrecurring_parent",
    "nonrecurring_profit_parent",
    "depreciation_amortization",
    "investment_income",
    "fair_value_change",
    "asset_disposal_gain",
    "EBIT_adjusted",
    "cash_flow_operating",
    "cash_paid_for_capex_ppe",
    "cash_received_from_asset_disposal",
    "cash_paid_for_acquisitions",
    "cash_paid_for_interest",
    "cash_paid_for_taxes",
    "cash_flow_investing",
    "cash_flow_financing",
}


@dataclass
class QuarterPoint:
    report_period: date
    fiscal_year: int
    quarter: int  # 1..4
    version_id: str | None
    version_chain: str | None
    available_date: date | None
    values: dict[str, float | None]
    flags: dict[str, str] = field(default_factory=dict)
    source_statements: list[FinancialStatement] = field(default_factory=list)


@dataclass
class AnnualPoint:
    report_period: date
    fiscal_year: int
    version_id: str | None
    version_chain: str | None
    available_date: date | None
    values: dict[str, float | None]
    source: FinancialStatement | None = None


@dataclass
class PeriodPanel:
    security_id: str
    quarters: list[QuarterPoint]
    annuals: list[AnnualPoint]
    ttm: list[QuarterPoint]  # TTM flow values aligned to quarter ends; stocks are period-end
    conversion_issues: list[str]
    flow_fields: set[str]


def _fiscal_year(d: date) -> int:
    return d.year


def _chain_id(stmt: FinancialStatement) -> str | None:
    if stmt.version_chain:
        return stmt.version_chain
    return stmt.version_id


def _scalar(stmt: FinancialStatement, name: str) -> float | None:
    return stmt.scalar(name)


def _period_end_for_type(year: int, stype: StatementType) -> date:
    q = stype.canonical()
    if q is StatementType.Q1:
        return date(year, 3, 31)
    if q is StatementType.H1:
        return date(year, 6, 30)
    if q is StatementType.Q3:
        return date(year, 9, 30)
    return date(year, 12, 31)


def build_period_panel(
    statements: list[FinancialStatement],
    config: ScreenConfig,
    *,
    flow_fields: Iterable[str] | None = None,
) -> PeriodPanel:
    """Build single-quarter and TTM series from visible statements (already PIT-filtered)."""
    flow = set(flow_fields or DEFAULT_FLOW_FIELDS)
    security_id = statements[0].security_id if statements else ""
    issues: list[str] = []

    # Index by (year, canonical type)
    by_year: dict[int, dict[StatementType, FinancialStatement]] = {}
    all_fields: set[str] = set()
    for stmt in statements:
        year = _fiscal_year(stmt.report_period)
        by_year.setdefault(year, {})
        key = stmt.statement_type.canonical()
        # Already version-selected; if duplicates remain, keep last
        by_year[year][key] = stmt
        all_fields.update(stmt.all_fields().keys())

    stock_fields = {f for f in all_fields if f not in flow}
    annuals: list[AnnualPoint] = []
    quarters: list[QuarterPoint] = []

    for year in sorted(by_year):
        slots = by_year[year]
        annual_stmt = slots.get(StatementType.ANNUAL)
        if annual_stmt is not None:
            annuals.append(
                AnnualPoint(
                    report_period=annual_stmt.report_period,
                    fiscal_year=year,
                    version_id=annual_stmt.version_id,
                    version_chain=_chain_id(annual_stmt),
                    available_date=annual_stmt.available_date,
                    values={name: _scalar(annual_stmt, name) for name in all_fields},
                    source=annual_stmt,
                )
            )

        q_points, year_issues = _quarters_for_year(year, slots, all_fields, flow, stock_fields)
        issues.extend(year_issues)
        quarters.extend(q_points)

    quarters.sort(key=lambda q: (q.report_period, q.quarter))
    ttm_points = _build_ttm(quarters, flow, stock_fields, int(config.require("screening.ttm_quarters")), issues)
    return PeriodPanel(
        security_id=security_id,
        quarters=quarters,
        annuals=annuals,
        ttm=ttm_points,
        conversion_issues=issues,
        flow_fields=flow,
    )


def _same_chain(a: FinancialStatement | None, b: FinancialStatement | None) -> bool:
    if a is None or b is None:
        return False
    ca, cb = _chain_id(a), _chain_id(b)
    if ca and cb:
        return ca == cb
    # If neither side is a restatement and version ids match or are both absent, allow.
    if not a.is_restatement and not b.is_restatement and (a.version_id == b.version_id or not a.version_id or not b.version_id):
        return True
    return a.version_id is not None and a.version_id == b.version_id


def _diff(later: float | None, earlier: float | None) -> float | None:
    if later is None or earlier is None:
        return None
    return later - earlier


def _quarters_for_year(
    year: int,
    slots: dict[StatementType, FinancialStatement],
    all_fields: set[str],
    flow: set[str],
    stock_fields: set[str],
) -> tuple[list[QuarterPoint], list[str]]:
    issues: list[str] = []
    q1 = slots.get(StatementType.Q1)
    h1 = slots.get(StatementType.H1)
    q3 = slots.get(StatementType.Q3)
    fy = slots.get(StatementType.ANNUAL)
    out: list[QuarterPoint] = []

    def gather(stmt: FinancialStatement | None, names: set[str]) -> dict[str, float | None]:
        if stmt is None:
            return {n: None for n in names}
        return {n: _scalar(stmt, n) for n in names}

    # Q1 single = Q1 cumulative
    if q1 is not None:
        values = {}
        flags = {}
        for name in all_fields:
            values[name] = _scalar(q1, name)
        out.append(
            QuarterPoint(
                report_period=q1.report_period,
                fiscal_year=year,
                quarter=1,
                version_id=q1.version_id,
                version_chain=_chain_id(q1),
                available_date=q1.available_date,
                values=values,
                flags=flags,
                source_statements=[q1],
            )
        )

    # Q2 = H1 - Q1 (same chain)
    if h1 is not None:
        values = {}
        flags = {}
        sources = [h1]
        can_diff = q1 is not None and _same_chain(h1, q1)
        if q1 is not None and not can_diff:
            issues.append(f"{year} Q2 conversion skipped: H1 and Q1 are not on the same version chain")
        for name in all_fields:
            if name in stock_fields:
                values[name] = _scalar(h1, name)
            elif can_diff:
                values[name] = _diff(_scalar(h1, name), _scalar(q1, name))
                sources = [h1, q1]
            else:
                values[name] = None
                flags[name] = "CROSS_VERSION_OR_MISSING"
        out.append(
            QuarterPoint(
                report_period=h1.report_period,
                fiscal_year=year,
                quarter=2,
                version_id=h1.version_id,
                version_chain=_chain_id(h1),
                available_date=h1.available_date,
                values=values,
                flags=flags,
                source_statements=sources,
            )
        )

    # Q3 = Q1-Q3 cumulative - H1
    if q3 is not None:
        values = {}
        flags = {}
        sources = [q3]
        can_diff = h1 is not None and _same_chain(q3, h1)
        if h1 is not None and not can_diff:
            issues.append(f"{year} Q3 conversion skipped: Q3 and H1 are not on the same version chain")
        for name in all_fields:
            if name in stock_fields:
                values[name] = _scalar(q3, name)
            elif can_diff:
                values[name] = _diff(_scalar(q3, name), _scalar(h1, name))
                sources = [q3, h1]
            else:
                values[name] = None
                flags[name] = "CROSS_VERSION_OR_MISSING"
        out.append(
            QuarterPoint(
                report_period=q3.report_period,
                fiscal_year=year,
                quarter=3,
                version_id=q3.version_id,
                version_chain=_chain_id(q3),
                available_date=q3.available_date,
                values=values,
                flags=flags,
                source_statements=sources,
            )
        )

    # Q4 = FY - Q1-Q3
    if fy is not None:
        values = {}
        flags = {}
        sources = [fy]
        can_diff = q3 is not None and _same_chain(fy, q3)
        if q3 is not None and not can_diff:
            issues.append(f"{year} Q4 conversion skipped: ANNUAL and Q3 are not on the same version chain")
        for name in all_fields:
            if name in stock_fields:
                values[name] = _scalar(fy, name)
            elif can_diff:
                values[name] = _diff(_scalar(fy, name), _scalar(q3, name))
                sources = [fy, q3]
            else:
                values[name] = None
                flags[name] = "CROSS_VERSION_OR_MISSING"
        out.append(
            QuarterPoint(
                report_period=fy.report_period,
                fiscal_year=year,
                quarter=4,
                version_id=fy.version_id,
                version_chain=_chain_id(fy),
                available_date=fy.available_date,
                values=values,
                flags=flags,
                source_statements=sources,
            )
        )

    return out, issues


def _build_ttm(
    quarters: list[QuarterPoint],
    flow: set[str],
    stock_fields: set[str],
    window: int,
    issues: list[str],
) -> list[QuarterPoint]:
    ttm: list[QuarterPoint] = []
    for i, q in enumerate(quarters):
        if i + 1 < window:
            continue
        window_qs = quarters[i + 1 - window : i + 1]
        # Require four consecutive fiscal quarters
        if not _consecutive(window_qs):
            issues.append(
                f"TTM at {q.report_period} skipped: window is not four consecutive quarters"
            )
            continue
        # Same visible version chain for flow sums: all four quarters must be convertible
        # (we already refused cross-version diffs). Additionally require no CROSS_VERSION flags
        # on the summed fields.
        values: dict[str, float | None] = {}
        flags: dict[str, str] = {}
        for name in set(flow) | set(stock_fields):
            if name in stock_fields:
                values[name] = q.values.get(name)
                continue
            parts = [p.values.get(name) for p in window_qs]
            if any(v is None for v in parts):
                values[name] = None
                flags[name] = "TTM_INCOMPLETE"
            else:
                values[name] = sum(parts)  # type: ignore[arg-type]
        ttm.append(
            QuarterPoint(
                report_period=q.report_period,
                fiscal_year=q.fiscal_year,
                quarter=q.quarter,
                version_id=q.version_id,
                version_chain=q.version_chain,
                available_date=q.available_date,
                values=values,
                flags=flags,
                source_statements=[s for p in window_qs for s in p.source_statements],
            )
        )
    return ttm


def _consecutive(window_qs: list[QuarterPoint]) -> bool:
    if not window_qs:
        return False
    seq: list[tuple[int, int]] = []
    for q in window_qs:
        seq.append((q.fiscal_year, q.quarter))
    for a, b in zip(seq, seq[1:]):
        exp_year, exp_q = a[0], a[1] + 1
        if exp_q == 5:
            exp_year, exp_q = a[0] + 1, 1
        if b != (exp_year, exp_q):
            return False
    return True


def latest_ttm(panel: PeriodPanel) -> QuarterPoint | None:
    return panel.ttm[-1] if panel.ttm else None


def latest_annual(panel: PeriodPanel) -> AnnualPoint | None:
    return panel.annuals[-1] if panel.annuals else None


def last_n_annuals(panel: PeriodPanel, n: int) -> list[AnnualPoint]:
    return panel.annuals[-n:] if n else []
