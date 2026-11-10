"""13.1.1 Cumulative quarters -> single-quarter values; same-version-chain only."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from avalue_screen.domain.enums import StatementType
from avalue_screen.domain.models import FieldValue, FinancialStatement
from avalue_screen.pit.period_builder import build_period_panel

SH = ZoneInfo("Asia/Shanghai")


def _fv(v):
    return FieldValue(value=v, unit="yuan", currency="CNY")


def _stmt(period, stype, version, revenue, *, chain=None, restatement=False, avail=None):
    return FinancialStatement(
        security_id="S",
        symbol="600000.SH",
        report_period=period,
        announcement_datetime=datetime(period.year if stype != "ANNUAL" else period.year + 1, 3, 31, 18, tzinfo=SH),
        available_date=avail or period,
        statement_type=StatementType(stype),
        version_id=version,
        version_chain=chain or version,
        is_restatement=restatement,
        income={"revenue": _fv(revenue), "net_income": _fv(revenue * 0.1)},
        balance={"total_assets": _fv(1000)},
        cashflow={"cash_flow_operating": _fv(revenue * 0.12)},
    )


def test_cumulative_to_single_quarter(config):
    year = 2024
    stmts = [
        _stmt(date(year, 3, 31), "Q1", "v1", 10),
        _stmt(date(year, 6, 30), "H1", "v1", 25),  # Q2 should be 15
        _stmt(date(year, 9, 30), "Q3", "v1", 40),  # Q3 should be 15
        _stmt(date(year, 12, 31), "ANNUAL", "v1", 60),  # Q4 should be 20
    ]
    panel = build_period_panel(stmts, config)
    by_q = {q.quarter: q for q in panel.quarters if q.fiscal_year == year}
    assert by_q[1].values["revenue"] == 10
    assert by_q[2].values["revenue"] == 15
    assert by_q[3].values["revenue"] == 15
    assert by_q[4].values["revenue"] == 20
    # Balance-sheet stocks are not differenced
    assert by_q[4].values["total_assets"] == 1000


def test_cross_version_difference_is_rejected(config):
    year = 2024
    stmts = [
        _stmt(date(year, 3, 31), "Q1", "v1", 10),
        _stmt(date(year, 6, 30), "H1", "v1", 25),
        _stmt(date(year, 9, 30), "Q3", "v1", 40),
        _stmt(date(year, 12, 31), "ANNUAL", "v2", 80, chain="v2", restatement=True),  # different chain
    ]
    panel = build_period_panel(stmts, config)
    q4 = next(q for q in panel.quarters if q.quarter == 4)
    assert q4.values["revenue"] is None
    assert q4.flags.get("revenue") == "CROSS_VERSION_OR_MISSING"
    assert any("Q4 conversion skipped" in msg for msg in panel.conversion_issues)
    # Annual totals themselves remain available
    assert panel.annuals[-1].values["revenue"] == 80
