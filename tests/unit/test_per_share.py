"""13.1.10 Share issuance / repurchase / stock dividends and per-share figures."""

from datetime import date

from avalue_screen.domain.enums import MetricStatus
from avalue_screen.features.per_share import eps_adjusted, market_cap


def test_eps_uses_diluted_shares(config):
    mv = eps_adjusted(100.0, 10.0, 8.0, config, window="TTM")
    assert mv.value == 10.0
    assert mv.status == MetricStatus.OK


def test_eps_falls_back_to_total_shares_with_flag(config):
    mv = eps_adjusted(100.0, None, 20.0, config, window="TTM")
    assert mv.value == 5.0
    assert mv.status == MetricStatus.SHARES_FALLBACK
    assert "SHARES_FALLBACK" in mv.flags


def test_market_cap_from_unadjusted_price_and_shares(config):
    mv = market_cap(2.5, 1_000.0, None, config, window="asof")
    assert mv.value == 2500.0


def test_dilution_reduces_eps_even_if_ni_grows(config):
    # NI +50%, shares +50% => EPS unchanged
    before = eps_adjusted(100.0, 100.0, 100.0, config, window="Y1")
    after = eps_adjusted(150.0, 150.0, 150.0, config, window="Y2")
    assert before.value == after.value == 1.0
