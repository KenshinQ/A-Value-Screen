"""13.1.2 TTM rolling calculation."""

from datetime import date

from avalue_screen.domain.enums import StatementType
from avalue_screen.domain.models import FieldValue, FinancialStatement
from avalue_screen.pit.period_builder import build_period_panel


def _fv(v):
    return FieldValue(value=v, unit="yuan", currency="CNY")


def _year_stmts(year, q_revs, version="v1"):
    # q_revs is 4 single-quarter revenues; convert to cumulative for statements
    cums = [sum(q_revs[:i]) for i in range(1, 5)]
    periods = [date(year, 3, 31), date(year, 6, 30), date(year, 9, 30), date(year, 12, 31)]
    types = ["Q1", "H1", "Q3", "ANNUAL"]
    out = []
    for p, t, c in zip(periods, types, cums):
        out.append(
            FinancialStatement(
                security_id="S",
                symbol="600000.SH",
                report_period=p,
                available_date=p,
                statement_type=StatementType(t),
                version_id=version,
                version_chain=version,
                income={"revenue": _fv(c)},
                balance={"total_assets": _fv(1.0)},
                cashflow={},
            )
        )
    return out


def test_ttm_is_sum_of_four_consecutive_quarters(config):
    stmts = _year_stmts(2023, [10, 20, 30, 40]) + _year_stmts(2024, [11, 21, 31, 41])
    panel = build_period_panel(stmts, config)
    # TTM at 2024 Q1 = 2023Q2+Q3+Q4 + 2024Q1 = 20+30+40+11 = 101
    ttm_q1 = next(t for t in panel.ttm if t.fiscal_year == 2024 and t.quarter == 1)
    assert ttm_q1.values["revenue"] == 101
    ttm_fy = next(t for t in panel.ttm if t.fiscal_year == 2024 and t.quarter == 4)
    assert ttm_fy.values["revenue"] == 11 + 21 + 31 + 41


def test_ttm_requires_consecutive_quarters(config):
    stmts = _year_stmts(2023, [10, 20, 30, 40])
    # drop 2023 Q2/H1
    stmts = [s for s in stmts if s.statement_type.value != "H1"]
    panel = build_period_panel(stmts, config)
    # Cannot form a 4-quarter TTM ending 2023 Q4 because Q2 is missing (gap)
    assert not any(t.fiscal_year == 2023 and t.quarter == 4 and t.values.get("revenue") for t in panel.ttm) or all(
        t.values.get("revenue") is None or not _consecutive_ok(panel)
        for t in panel.ttm
        if t.fiscal_year == 2023 and t.quarter == 4
    )
    # Explicit: no TTM point at FY2023 if quarters are not consecutive
    fy_ttm = [t for t in panel.ttm if t.fiscal_year == 2023 and t.quarter == 4]
    assert fy_ttm == []


def _consecutive_ok(panel):
    return False
