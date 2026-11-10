"""13.1.12 Boundary values of every scoring band (left-closed, right-open)."""

import pytest

from avalue_screen.utils import score_from_bands


def _bands(config, path):
    return config.require(path)


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.15, 8),
        (0.20, 8),
        (0.12, 6),
        (0.149999, 6),
        (0.10, 4),
        (0.08, 2),
        (0.07999, 0),
        (0.0, 0),
    ],
)
def test_roic_median_bands(config, value, expected):
    sc, _ = score_from_bands(value, _bands(config, "scoring.commercial_quality.roic_median.bands"))
    assert sc == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.50, 10),
        (0.40, 9),
        (0.30, 7),
        (0.20, 4),
        (0.10, 2),
        (0.099, 0),
        (-0.2, 0),
    ],
)
def test_mos_bands(config, value, expected):
    sc, _ = score_from_bands(value, _bands(config, "scoring.valuation_attractiveness.margin_of_safety.bands"))
    assert sc == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (1.0, 5),
        (0.8, 4),
        (0.5, 2),
        (0.499, 0),
    ],
)
def test_cash_conversion_bands(config, value, expected):
    sc, band = score_from_bands(value, _bands(config, "scoring.earnings_quality.cash_conversion.bands"))
    assert sc == expected
    if value < 0.5:
        assert band.get("review") is True


@pytest.mark.parametrize(
    "value,expected",
    [
        (8.0, 4),
        (4.0, 3),
        (2.0, 2),
        (1.5, 1),
        (1.499, 0),
    ],
)
def test_interest_coverage_bands(config, value, expected):
    sc, _ = score_from_bands(value, _bands(config, "scoring.financial_health.interest_coverage.bands"))
    assert sc == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.08, 5),
        (0.06, 4),
        (0.04, 3),
        (0.02, 1),
        (0.019, 0),
    ],
)
def test_fcf_yield_bands(config, value, expected):
    sc, _ = score_from_bands(value, _bands(config, "scoring.valuation_attractiveness.fcf_yield.bands"))
    assert sc == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.05, 6),
        (0.03, 5),
        (0.01, 3),
        (0.00, 1),
        (-0.01, 0),
    ],
)
def test_reverse_dcf_bands(config, value, expected):
    sc, _ = score_from_bands(value, _bands(config, "scoring.valuation_attractiveness.reverse_dcf.bands"))
    assert sc == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (1.5, 3),
        (1.0, 2),
        (0.5, 1),
        (0.49, 0),
    ],
)
def test_cash_cover_bands(config, value, expected):
    sc, _ = score_from_bands(value, _bands(config, "scoring.financial_health.cash_short_debt_cover.bands"))
    assert sc == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.30, 4),
        (0.20, 3),
        (0.10, 2),
        (0.00, 1),
        (-0.01, 0),
    ],
)
def test_ev_ebit_discount_bands(config, value, expected):
    sc, _ = score_from_bands(value, _bands(config, "scoring.valuation_attractiveness.ev_ebit_discount.bands"))
    assert sc == expected


def test_missing_value_does_not_score_as_zero():
    sc, band = score_from_bands(None, [{"min": None, "max": 0.08, "score": 0}])
    assert sc is None
    assert band is None
