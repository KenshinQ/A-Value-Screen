"""Unit and currency normalization. Inconsistent units must not be silently mixed."""

import pytest

from avalue_screen.domain.errors import UnitNormalizationError
from avalue_screen.domain.models import FieldValue
from avalue_screen.ingestion.unit_normalizer import normalize_field_value, normalize_numeric


def test_amount_units_to_yuan(config):
    assert normalize_numeric(2.0, "ten_thousand_yuan", config) == 20_000.0
    assert normalize_numeric(3.0, "万元", config) == 30_000.0
    assert normalize_numeric(1.0, "million_yuan", config) == 1_000_000.0


def test_percent_to_decimal(config):
    fv = FieldValue(value=10.0, unit="percent")
    out = normalize_field_value(fv, config, field="gross_margin")
    assert out.value == 0.10
    assert out.unit == "ratio"


def test_foreign_currency_is_an_error(config):
    with pytest.raises(UnitNormalizationError):
        normalize_numeric(1.0, "yuan", config, currency="USD")


def test_unknown_unit_is_an_error(config):
    with pytest.raises(UnitNormalizationError):
        normalize_numeric(1.0, "boxes", config)


def test_missing_stays_missing(config):
    fv = FieldValue(value=None, unit="yuan")
    out = normalize_field_value(fv, config)
    assert out.value is None
