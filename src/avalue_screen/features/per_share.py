"""Per-share and market-value formulas."""

from __future__ import annotations

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import MetricStatus
from avalue_screen.domain.models import MetricValue
from avalue_screen.utils import cagr, is_finite


def eps_adjusted(
    ni_ex_nr_ttm: float | None,
    diluted_shares: float | None,
    total_shares: float | None,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    version = config.formula_version("CAGR")
    flags: list[str] = []
    shares = diluted_shares
    status = MetricStatus.OK
    if not is_finite(shares):
        shares = total_shares
        flags.append("SHARES_FALLBACK")
        status = MetricStatus.SHARES_FALLBACK
    if not is_finite(ni_ex_nr_ttm) or not is_finite(shares) or shares <= 0:  # type: ignore[operator]
        return MetricValue(
            name="EPS_adjusted",
            value=None,
            status=MetricStatus.MISSING if not is_finite(ni_ex_nr_ttm) or not is_finite(shares) else MetricStatus.INVALID,
            window=window,
            inputs={"net_income_ex_nonrecurring_parent_TTM": ni_ex_nr_ttm, "shares": shares},
        )
    return MetricValue(
        name="EPS_adjusted",
        value=ni_ex_nr_ttm / shares,  # type: ignore[operator]
        status=status,
        window=window,
        inputs={
            "net_income_ex_nonrecurring_parent_TTM": ni_ex_nr_ttm,
            "diluted_shares": diluted_shares,
            "total_shares": total_shares,
        },
        flags=flags,
    )


def market_cap(
    close_unadjusted: float | None,
    total_shares: float | None,
    provided: float | None,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    if is_finite(provided) and provided is not None and provided > 0:
        return MetricValue(
            name="market_cap",
            value=provided,
            status=MetricStatus.OK,
            window=window,
            inputs={"market_cap_provided": provided},
        )
    if not is_finite(close_unadjusted) or not is_finite(total_shares):
        return MetricValue(
            name="market_cap",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            inputs={"close_unadjusted": close_unadjusted, "total_shares": total_shares},
        )
    return MetricValue(
        name="market_cap",
        value=close_unadjusted * total_shares,  # type: ignore[operator]
        status=MetricStatus.OK,
        window=window,
        formula_id="EV",
        formula_version=config.formula_version("EV"),
        inputs={"close_unadjusted": close_unadjusted, "total_shares": total_shares},
    )


def enterprise_value(
    mcap: float | None,
    debt: float | None,
    cash: float | None,
    eligible_non_op: float | None,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    version = config.formula_version("EV")
    eligible = eligible_non_op or 0.0
    if not is_finite(mcap) or not is_finite(debt) or not is_finite(cash):
        return MetricValue(
            name="enterprise_value",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="EV",
            formula_version=version,
        )
    return MetricValue(
        name="enterprise_value",
        value=mcap + debt - cash - eligible,  # type: ignore[operator]
        status=MetricStatus.OK,
        window=window,
        formula_id="EV",
        formula_version=version,
        inputs={
            "market_cap": mcap,
            "interest_bearing_debt": debt,
            "unrestricted_cash": cash,
            "eligible_non_operating_financial_assets": eligible,
        },
    )


def multiple(numer: float | None, denom: float | None, name: str, config: ScreenConfig, *, window: str) -> MetricValue:
    if not is_finite(numer) or not is_finite(denom):
        return MetricValue(name=name, value=None, status=MetricStatus.MISSING, window=window)
    if denom is None or denom <= 0:
        return MetricValue(
            name=name,
            value=None,
            status=MetricStatus.NA_NEGATIVE_DENOMINATOR,
            window=window,
            notes="Denominator <= 0; multiple is NA_NEGATIVE_DENOMINATOR and must not be ranked as a negative PE",
            inputs={"numerator": numer, "denominator": denom},
        )
    return MetricValue(
        name=name,
        value=numer / denom,
        status=MetricStatus.OK,
        window=window,
        inputs={"numerator": numer, "denominator": denom},
    )


def yoy_growth(current: float | None, prior: float | None, name: str, *, window: str) -> MetricValue:
    if not is_finite(current) or not is_finite(prior):
        return MetricValue(name=name, value=None, status=MetricStatus.MISSING, window=window)
    if prior is None or prior == 0:
        return MetricValue(
            name=name,
            value=None,
            status=MetricStatus.INVALID,
            window=window,
            notes="Prior-period denominator is zero; YoY growth not computed",
        )
    if prior < 0 or (current is not None and current < 0 and prior > 0):
        status = MetricStatus.TURNAROUND
    else:
        status = MetricStatus.OK
    return MetricValue(
        name=name,
        value=(current / prior) - 1.0,  # type: ignore[operator]
        status=status,
        window=window,
        inputs={"current": current, "prior": prior},
    )


def cagr_metric(start: float | None, end: float | None, years: float, name: str) -> MetricValue:
    value, status = cagr(start, end, years)
    return MetricValue(
        name=name,
        value=value,
        status=status,
        window=f"{years}y",
        formula_id="CAGR",
        formula_version="cagr_v1",
        inputs={"start": start, "end": end, "years": years},
        notes=None if status is not MetricStatus.TURNAROUND else "CAGR undefined across non-positive values; use TURNAROUND description",
    )
