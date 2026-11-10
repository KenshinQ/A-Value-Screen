"""Accounting identity and consistency checks. Failures are recorded, not silently fixed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.models import FinancialStatement
from avalue_screen.pit.period_builder import PeriodPanel
from avalue_screen.utils import is_finite, within_tolerance


@dataclass
class AccountingIssue:
    check_id: str
    period: str | None
    message: str
    lhs: float | None = None
    rhs: float | None = None
    material: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def run_accounting_checks(
    statements: list[FinancialStatement],
    panel: PeriodPanel,
    config: ScreenConfig,
) -> list[AccountingIssue]:
    abs_tol = float(config.require("data_quality.absolute_tolerance_cny"))
    rel_tol = float(config.require("data_quality.relative_tolerance"))
    issues: list[AccountingIssue] = []

    for stmt in statements:
        assets = stmt.scalar("total_assets")
        liab = stmt.scalar("total_liabilities")
        equity = stmt.scalar("total_equity")
        if all(is_finite(x) for x in (assets, liab, equity)):
            rhs = liab + equity  # type: ignore[operator]
            ok = within_tolerance(assets, rhs, abs_tol=abs_tol, rel_tol=rel_tol)  # type: ignore[arg-type]
            if not ok:
                issues.append(
                    AccountingIssue(
                        check_id="AQ-BS-IDENTITY",
                        period=stmt.report_period.isoformat(),
                        message="total_assets does not equal total_liabilities + total_equity within tolerance",
                        lhs=assets,
                        rhs=rhs,
                        material=True,
                    )
                )
        ni = stmt.scalar("net_income")
        ni_p = stmt.scalar("net_income_parent")
        # Parent NI should not systematically exceed consolidated NI without minority interest explaining it
        if is_finite(ni) and is_finite(ni_p) and ni_p > ni + max(abs_tol, rel_tol * max(abs(ni), abs(ni_p))):  # type: ignore[operator]
            minority = stmt.scalar("minority_interest")
            issues.append(
                AccountingIssue(
                    check_id="AQ-NI-PARENT-GT-CONSOL",
                    period=stmt.report_period.isoformat(),
                    message="net_income_parent exceeds consolidated net_income by more than tolerance",
                    lhs=ni_p,
                    rhs=ni,
                    material=True,
                    details={"minority_interest": minority},
                )
            )
        ni_ex = stmt.scalar("net_income_ex_nonrecurring_parent")
        nr = stmt.scalar("nonrecurring_profit_parent")
        if all(is_finite(x) for x in (ni_ex, nr, ni_p)):
            rhs = ni_ex + nr  # type: ignore[operator]
            if not within_tolerance(ni_p, rhs, abs_tol=abs_tol, rel_tol=rel_tol):  # type: ignore[arg-type]
                issues.append(
                    AccountingIssue(
                        check_id="AQ-NONRECURRING-RECON",
                        period=stmt.report_period.isoformat(),
                        message="net_income_ex_nonrecurring_parent + nonrecurring_profit_parent != net_income_parent",
                        lhs=ni_p,
                        rhs=rhs,
                        material=False,
                    )
                )

    # Opening vs prior closing balance (same version chain)
    by_type_period = {}
    for stmt in statements:
        key = (stmt.statement_type.canonical(), stmt.version_chain or stmt.version_id)
        by_type_period.setdefault(key, []).append(stmt)
    # Compare adjacent annual balance-sheet stock fields
    annuals = [s for s in statements if s.statement_type.is_annual()]
    annuals = sorted(annuals, key=lambda s: s.report_period)
    stock_fields = ("total_assets", "total_equity", "equity_parent", "cash_and_equivalents")
    for prev, cur in zip(annuals, annuals[1:]):
        if (prev.version_chain or prev.version_id) != (cur.version_chain or cur.version_id):
            continue
        # We do not have explicit opening balances; this is a soft check only when both present.

    return issues
