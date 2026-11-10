"""Helpers that assemble valuation starting points from feature history."""

from __future__ import annotations

from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.utils import is_finite, median


def ratio_to_revenue(values: list[float | None], revenues: list[float | None]) -> list[float]:
    out: list[float] = []
    for v, r in zip(values, revenues):
        if is_finite(v) and is_finite(r) and r not in (None, 0):
            out.append(v / r)  # type: ignore[operator]
    return out


def median_ratio(values: list[float | None], revenues: list[float | None]) -> float | None:
    return median(ratio_to_revenue(values, revenues))
