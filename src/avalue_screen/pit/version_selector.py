"""Select the latest visible version of each report period as of a screening date."""

from __future__ import annotations

from datetime import date

from avalue_screen.domain.enums import StatementType
from avalue_screen.domain.models import FinancialStatement


def _type_key(stmt: FinancialStatement) -> StatementType:
    return stmt.statement_type.canonical()


def select_visible_statements(
    statements: list[FinancialStatement],
    as_of: date,
) -> list[FinancialStatement]:
    """For each (report_period, canonical statement_type), keep the latest version with available_date <= as_of.

    Later restatements whose available_date is after as_of are invisible.
    Ties are broken by announcement_datetime, then version_id.
    """
    visible = [s for s in statements if s.available_date is not None and s.available_date <= as_of]
    best: dict[tuple, FinancialStatement] = {}
    for stmt in visible:
        key = (stmt.security_id, stmt.report_period, _type_key(stmt))
        prev = best.get(key)
        if prev is None or _is_newer(stmt, prev):
            best[key] = stmt
    return sorted(best.values(), key=lambda s: (s.report_period, s.statement_type.fiscal_quarter_end()))


def _is_newer(candidate: FinancialStatement, current: FinancialStatement) -> bool:
    if candidate.available_date != current.available_date:
        return (candidate.available_date or date.min) > (current.available_date or date.min)
    ca = candidate.announcement_datetime
    cu = current.announcement_datetime
    if ca is not None and cu is not None and ca != cu:
        return ca > cu
    if ca is not None and cu is None:
        return True
    # Stable last-resort: version_id lexicographic (v2 > v1 typically)
    return str(candidate.version_id or "") > str(current.version_id or "")
