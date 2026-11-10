"""13.1.6 ROIC, ROE, EBITDA, net debt, interest coverage."""

from avalue_screen.domain.enums import MetricStatus
from avalue_screen.features import balance_sheet as bs
from avalue_screen.features import cashflow as cf
from avalue_screen.features import profitability as pf


def test_ebit_derived_not_equal_to_pbt(config):
    fields = {
        "operating_profit": 100.0,
        "interest_expense_gross": 10.0,
        "interest_income": 2.0,
        "investment_income": 5.0,
        "fair_value_change": 0.0,
        "asset_disposal_gain": 0.0,
    }
    mv = pf.ebit_adjusted(fields, config, window="FY")
    # 100 + 10 - 2 - 5 = 103; must not equal profit_before_tax if someone passed PBT
    assert mv.value == 103
    assert mv.status == MetricStatus.OK


def test_vendor_ebit_used_when_present(config):
    fields = {"EBIT_adjusted": 77.0, "operating_profit": 1.0}
    mv = pf.ebit_adjusted(fields, config, window="FY")
    assert mv.value == 77.0
    assert "Vendor-provided EBIT_adjusted" in (mv.notes or "")


def test_roic_and_roe(config):
    roic = pf.roic_from_parts(150.0, 800.0, 700.0, config, window="FY")
    assert abs(roic.value - 150.0 / 750.0) < 1e-12
    roe = pf.roe_from_parts(80.0, 500.0, 500.0, config, window="FY")
    assert roe.value == 80.0 / 500.0


def test_roic_invalid_when_capital_nonpositive(config):
    roic = pf.roic_from_parts(10.0, -5.0, -5.0, config, window="FY")
    assert roic.value is None
    assert roic.status == MetricStatus.NA_NONPOSITIVE_CAPITAL


def test_roe_invalid_when_equity_nonpositive(config):
    roe = pf.roe_from_parts(10.0, -1.0, 0.0, config, window="FY")
    assert roe.status == MetricStatus.NA_NONPOSITIVE_CAPITAL
    assert roe.value is None


def test_ebitda_net_debt_interest_coverage(config):
    ebitda = cf.ebitda(100.0, 20.0, config, window="FY")
    assert ebitda.value == 120.0
    nd = cf.net_debt(80.0, 100.0, config, window="FY")
    assert nd.value == -20.0
    assert nd.status == MetricStatus.NET_CASH
    ratio = cf.net_debt_to_ebitda(-20.0, 120.0, config, window="FY")
    assert ratio.status == MetricStatus.NET_CASH
    cov = cf.interest_coverage(100.0, 10.0, config, window="FY")
    assert cov.value == 10.0


def test_interest_coverage_near_zero_is_na_not_infinite(config):
    cov = cf.interest_coverage(100.0, 0.0, config, window="FY")
    assert cov.value is None
    assert cov.status == MetricStatus.NA_NO_INTEREST


def test_leverage_uninterpretable_when_ebitda_nonpositive_and_net_debt_positive(config):
    ratio = cf.net_debt_to_ebitda(50.0, -10.0, config, window="FY")
    assert ratio.value is None
    assert ratio.status == MetricStatus.NA_UNINTERPRETABLE_RATIO
    assert "LEVERAGE_UNINTERPRETABLE" in ratio.flags


def test_nopat_and_tax_fallback(config):
    tax = pf.normalized_tax_rate([0.25, 0.25, 0.24], config)
    assert abs(tax.value - 0.25) < 0.01
    tax_fb = pf.normalized_tax_rate([], config)
    assert tax_fb.status == MetricStatus.TAX_RATE_FALLBACK
    assert tax_fb.value == 0.25
    nopat = pf.nopat(200.0, 0.25, config, window="FY")
    assert nopat.value == 150.0
