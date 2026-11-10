"""13.1.8 Receivable days, inventory days, operating NWC change."""

from avalue_screen.domain.enums import MetricStatus
from avalue_screen.features.balance_sheet import operating_nwc, trade_receivables, turnover_days


def test_trade_receivables_sum(config):
    fields = {
        "accounts_receivable": 10.0,
        "notes_receivable": 3.0,
        "accounts_receivable_financing": 2.0,
        "contract_assets": 1.0,
    }
    mv = trade_receivables(fields, config, window="FY")
    assert mv.value == 16.0


def test_turnover_days(config):
    days = turnover_days(100.0, 365.0, 365.0, "receivable_days", config, window="TTM")
    assert days.value == 100.0
    invalid = turnover_days(100.0, 0.0, 365.0, "inventory_days", config, window="TTM")
    assert invalid.value is None
    assert invalid.status == MetricStatus.INVALID


def test_operating_nwc_and_delta(config):
    nwc_t = operating_nwc(40.0, 30.0, 20.0, 5.0, 5.0, config, window="FY")
    nwc_t1 = operating_nwc(30.0, 25.0, 20.0, 5.0, 5.0, config, window="FY-1")
    assert nwc_t.value == 40  # 40+30-20-5-5
    assert nwc_t1.value == 25
    assert nwc_t.value - nwc_t1.value == 15
