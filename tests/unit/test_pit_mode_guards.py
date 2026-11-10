"""Non-PIT final-vintage data cannot be used for historical_pit_backtest."""

import pytest

from avalue_screen.domain.enums import PitStatus, RunMode
from avalue_screen.domain.errors import InsufficientPitHistoryError
from avalue_screen.domain.models import CompanyInput, FinancialStatement, UniversePayload
from avalue_screen.domain.enums import StatementType
from avalue_screen.pit.availability import assess_pit_capability
from datetime import date


def test_historical_pit_backtest_errors_when_versions_missing():
    company = CompanyInput(
        security_id="X",
        symbol="600000.SH",
        final_vintage_only=True,
        statements=[
            FinancialStatement(
                security_id="X",
                symbol="600000.SH",
                report_period=date(2022, 12, 31),
                statement_type=StatementType.ANNUAL,
                # no announcement, no version
            )
        ],
    )
    payload = UniversePayload(companies=[company], final_vintage_only=True)
    with pytest.raises(InsufficientPitHistoryError, match="refusing to fall back"):
        assess_pit_capability(payload, RunMode.HISTORICAL_PIT_BACKTEST)


def test_restatement_without_prior_version_fails_historical_pit():
    from avalue_screen.domain.models import FieldValue
    from datetime import datetime
    from zoneinfo import ZoneInfo

    stmt = FinancialStatement(
        security_id="X",
        symbol="600000.SH",
        report_period=date(2022, 12, 31),
        announcement_datetime=datetime(2024, 3, 31, 18, tzinfo=ZoneInfo("Asia/Shanghai")),
        available_date=date(2024, 4, 1),
        statement_type=StatementType.ANNUAL,
        version_id="v2",
        is_restatement=True,
        income={"net_income": FieldValue(value=1.0)},
    )
    payload = UniversePayload(companies=[CompanyInput(security_id="X", symbol="600000.SH", statements=[stmt])])
    with pytest.raises(InsufficientPitHistoryError):
        assess_pit_capability(payload, RunMode.HISTORICAL_PIT_BACKTEST)


def test_final_vintage_research_is_stamped_non_pit():
    company = CompanyInput(
        security_id="X",
        symbol="600000.SH",
        final_vintage_only=True,
        statements=[
            FinancialStatement(
                security_id="X",
                symbol="600000.SH",
                report_period=date(2022, 12, 31),
                statement_type=StatementType.ANNUAL,
            )
        ],
    )
    payload = UniversePayload(companies=[company], final_vintage_only=True)
    assessment = assess_pit_capability(payload, RunMode.FINAL_VINTAGE_RESEARCH)
    assert assessment.pit_status is PitStatus.NON_PIT_FINAL_VINTAGE
