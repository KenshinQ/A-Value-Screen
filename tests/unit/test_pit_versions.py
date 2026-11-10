"""13.1.3 Version selection around restatements."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from avalue_screen.domain.enums import StatementType
from avalue_screen.domain.models import FieldValue, FinancialStatement
from avalue_screen.pit.version_selector import select_visible_statements

SH = ZoneInfo("Asia/Shanghai")


def _stmt(avail, version, value, restatement=False):
    return FinancialStatement(
        security_id="R",
        symbol="600004.SH",
        report_period=date(2022, 12, 31),
        announcement_datetime=datetime(avail.year, avail.month, avail.day, 9, tzinfo=SH),
        available_date=avail,
        statement_type=StatementType.ANNUAL,
        version_id=version,
        version_chain=version,
        is_restatement=restatement,
        income={"net_income": FieldValue(value=value, unit="yuan", currency="CNY")},
    )


def test_selects_latest_visible_version_only():
    old = _stmt(date(2023, 4, 3), "v1", 100)
    new = _stmt(date(2024, 4, 1), "v2", 50, restatement=True)
    # Before restatement is available
    vis = select_visible_statements([old, new], date(2023, 6, 30))
    assert len(vis) == 1
    assert vis[0].version_id == "v1"
    assert vis[0].scalar("net_income") == 100
    # After restatement
    vis2 = select_visible_statements([old, new], date(2024, 6, 30))
    assert len(vis2) == 1
    assert vis2[0].version_id == "v2"
    assert vis2[0].scalar("net_income") == 50


def test_future_version_does_not_leak_into_past_as_of():
    old = _stmt(date(2023, 4, 3), "v1", 100)
    vis = select_visible_statements([old], date(2023, 6, 30))
    new = _stmt(date(2024, 4, 1), "v2", 50, restatement=True)
    vis_after_insert = select_visible_statements([old, new], date(2023, 6, 30))
    assert vis[0].scalar("net_income") == vis_after_insert[0].scalar("net_income") == 100
    assert vis_after_insert[0].version_id == "v1"
