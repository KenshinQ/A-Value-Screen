"""13.1.7 CFO / NI and FCF / adjusted NI conversion."""

from avalue_screen.domain.enums import MetricStatus
from avalue_screen.features.cashflow import capex, conversion_ratio, fcf


def test_capex_sign_normalized(config):
    mv = capex(-80.0, config, window="FY")
    assert mv.value == 80.0
    assert "CAPEX_SIGN_NORMALIZED" in mv.flags


def test_fcf(config):
    assert fcf(120.0, 40.0, config, window="FY").value == 80.0


def test_cash_conversion_ratio(config):
    ok = conversion_ratio(120.0, 100.0, "cash_conversion_5y", "cash_conversion_5y", config, window="5y")
    assert ok.value == 1.2
    bad = conversion_ratio(50.0, -10.0, "cash_conversion_5y", "cash_conversion_5y", config, window="5y")
    assert bad.value is None
    assert bad.status == MetricStatus.NA_UNINTERPRETABLE_RATIO
    tiny = conversion_ratio(1.0, 0.0, "fcf_conversion_5y", "fcf_conversion_5y", config, window="5y")
    assert tiny.status == MetricStatus.NA_UNINTERPRETABLE_RATIO


def test_missing_not_filled_with_zero(config):
    mv = fcf(None, 10.0, config, window="FY")
    assert mv.value is None
    assert mv.status == MetricStatus.MISSING
