"""13.1.9 Negative profit, negative FCF, negative invested capital, zero denominators."""

from avalue_screen.domain.enums import MetricStatus
from avalue_screen.features.cashflow import fcf, net_debt_to_ebitda
from avalue_screen.features.per_share import cagr_metric, multiple, yoy_growth
from avalue_screen.features.profitability import margin, roic_from_parts


def test_negative_profit_margins_and_multiples(config):
    m = margin(-10.0, 100.0, "operating_margin", config, window="FY")
    assert m.value == -0.1
    m_bad = margin(10.0, 0.0, "operating_margin", config, window="FY")
    assert m_bad.value is None
    pe = multiple(100.0, -5.0, "normalized_pe", config, window="TTM")
    assert pe.value is None
    assert pe.status == MetricStatus.NA_NEGATIVE_DENOMINATOR


def test_negative_fcf_is_kept_not_zeroed(config):
    mv = fcf(10.0, 40.0, config, window="FY")
    assert mv.value == -30.0


def test_zero_denominator_growth_and_cagr(config):
    g = yoy_growth(10.0, 0.0, "revenue_growth_yoy", window="FY")
    assert g.value is None
    c = cagr_metric(-10.0, 20.0, 4, "cagr")
    assert c.value is None
    assert c.status == MetricStatus.TURNAROUND


def test_negative_invested_capital(config):
    roic = roic_from_parts(10.0, -20.0, -10.0, config, window="FY")
    assert roic.value is None
    assert roic.status == MetricStatus.NA_NONPOSITIVE_CAPITAL
