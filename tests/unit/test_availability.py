"""13.1.4-5 Announcement availability, including after-hours next session."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from avalue_screen.pit.availability import compute_available_date
from avalue_screen.utils import TradingCalendar

SH = ZoneInfo("Asia/Shanghai")


def test_after_hours_announcement_effective_next_trading_day(config):
    calendar = TradingCalendar()
    # 2024-03-29 is a Friday
    friday = date(2024, 3, 29)
    assert calendar.is_trading_day(friday)
    dt = datetime(2024, 3, 29, 18, 0, tzinfo=SH)
    avail = compute_available_date(dt, calendar, config)
    assert avail == date(2024, 4, 1)  # Monday after weekend


def test_before_close_same_trading_day(config):
    calendar = TradingCalendar()
    dt = datetime(2024, 3, 29, 14, 30, tzinfo=SH)
    avail = compute_available_date(dt, calendar, config)
    assert avail == date(2024, 3, 29)


def test_date_only_announcement_is_conservative_next_session(config):
    calendar = TradingCalendar()
    avail = compute_available_date("2024-03-29", calendar, config)
    assert avail == date(2024, 4, 1)


def test_financials_usable_only_after_available_date(config):
    from avalue_screen.domain.enums import StatementType
    from avalue_screen.domain.models import FieldValue, FinancialStatement
    from avalue_screen.pit.availability import statement_is_visible

    stmt = FinancialStatement(
        security_id="S",
        symbol="X",
        report_period=date(2023, 12, 31),
        available_date=date(2024, 4, 1),
        statement_type=StatementType.ANNUAL,
        version_id="v1",
        income={"revenue": FieldValue(value=1.0)},
    )
    assert statement_is_visible(stmt, date(2024, 4, 1))
    assert not statement_is_visible(stmt, date(2024, 3, 31))
