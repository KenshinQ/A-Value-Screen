"""Cash-flow formulas: capex, FCF, conversion ratios, FCFF, coverage."""

from __future__ import annotations

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import MetricStatus
from avalue_screen.domain.models import MetricValue
from avalue_screen.utils import is_finite


def capex(cash_paid_for_capex_ppe: float | None, config: ScreenConfig, *, window: str) -> MetricValue:
    if not is_finite(cash_paid_for_capex_ppe):
        return MetricValue(
            name="capex",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="FCF",
            formula_version=config.formula_version("FCF"),
        )
    value = abs(cash_paid_for_capex_ppe)  # type: ignore[arg-type]
    flags = []
    if cash_paid_for_capex_ppe < 0:  # type: ignore[operator]
        flags.append("CAPEX_SIGN_NORMALIZED")
    return MetricValue(
        name="capex",
        value=value,
        status=MetricStatus.OK,
        window=window,
        formula_id="FCF",
        formula_version=config.formula_version("FCF"),
        inputs={"cash_paid_for_capex_ppe": cash_paid_for_capex_ppe},
        flags=flags,
    )


def fcf(cfo: float | None, capex_value: float | None, config: ScreenConfig, *, window: str) -> MetricValue:
    if not is_finite(cfo) or not is_finite(capex_value):
        return MetricValue(
            name="FCF",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="FCF",
            formula_version=config.formula_version("FCF"),
            inputs={"cash_flow_operating": cfo, "capex": capex_value},
        )
    return MetricValue(
        name="FCF",
        value=cfo - capex_value,  # type: ignore[operator]
        status=MetricStatus.OK,
        window=window,
        formula_id="FCF",
        formula_version=config.formula_version("FCF"),
        inputs={"cash_flow_operating": cfo, "capex": capex_value},
    )


def conversion_ratio(
    numer_sum: float | None,
    denom_sum: float | None,
    name: str,
    formula_id: str,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    min_abs = float(config.require("quality_gates.conversion_denominator_min_abs"))
    version = config.formula_version(formula_id)
    if not is_finite(numer_sum) or not is_finite(denom_sum):
        return MetricValue(
            name=name,
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id=formula_id,
            formula_version=version,
            inputs={"numerator_sum": numer_sum, "denominator_sum": denom_sum},
        )
    if denom_sum is None or abs(denom_sum) < min_abs or denom_sum <= 0:
        return MetricValue(
            name=name,
            value=None,
            status=MetricStatus.NA_UNINTERPRETABLE_RATIO,
            window=window,
            formula_id=formula_id,
            formula_version=version,
            notes=(
                "Conversion ratio is uninterpretable because the cumulative denominator "
                "is not reliably positive; raw cumulative amounts are retained"
            ),
            inputs={"numerator_sum": numer_sum, "denominator_sum": denom_sum},
        )
    return MetricValue(
        name=name,
        value=numer_sum / denom_sum,
        status=MetricStatus.OK,
        window=window,
        formula_id=formula_id,
        formula_version=version,
        inputs={"numerator_sum": numer_sum, "denominator_sum": denom_sum},
    )


def ebitda(ebit: float | None, da: float | None, config: ScreenConfig, *, window: str) -> MetricValue:
    version = config.formula_version("EBITDA")
    if not is_finite(ebit):
        return MetricValue(name="EBITDA", value=None, status=MetricStatus.MISSING, window=window, formula_id="EBITDA", formula_version=version)
    if not is_finite(da):
        return MetricValue(
            name="EBITDA",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="EBITDA",
            formula_version=version,
            notes="depreciation_amortization missing; EBITDA not fabricated",
            inputs={"EBIT_adjusted": ebit, "depreciation_amortization": da},
        )
    return MetricValue(
        name="EBITDA",
        value=ebit + da,  # type: ignore[operator]
        status=MetricStatus.OK,
        window=window,
        formula_id="EBITDA",
        formula_version=version,
        inputs={"EBIT_adjusted": ebit, "depreciation_amortization": da},
    )


def net_debt(debt: float | None, cash: float | None, config: ScreenConfig, *, window: str) -> MetricValue:
    version = config.formula_version("net_debt")
    if not is_finite(debt) or not is_finite(cash):
        return MetricValue(name="net_debt", value=None, status=MetricStatus.MISSING, window=window, formula_id="net_debt", formula_version=version)
    value = debt - cash  # type: ignore[operator]
    status = MetricStatus.NET_CASH if value < 0 else MetricStatus.OK
    return MetricValue(
        name="net_debt",
        value=value,
        status=status,
        window=window,
        formula_id="net_debt",
        formula_version=version,
        inputs={"interest_bearing_debt": debt, "unrestricted_cash": cash},
        flags=["NET_CASH"] if value < 0 else [],
    )


def net_debt_to_ebitda(nd: float | None, ebitda_v: float | None, config: ScreenConfig, *, window: str) -> MetricValue:
    version = config.formula_version("net_debt")
    if not is_finite(nd) or not is_finite(ebitda_v):
        return MetricValue(name="net_debt_to_ebitda", value=None, status=MetricStatus.MISSING, window=window, formula_id="net_debt", formula_version=version)
    if nd is not None and nd < 0:
        return MetricValue(
            name="net_debt_to_ebitda",
            value=nd / ebitda_v if ebitda_v not in (0, None) else None,
            status=MetricStatus.NET_CASH,
            window=window,
            formula_id="net_debt",
            formula_version=version,
            notes="Net cash company; leverage is scored as net cash, not as a negative ratio extreme",
            inputs={"net_debt": nd, "EBITDA": ebitda_v},
            flags=["NET_CASH"],
        )
    if ebitda_v is None or ebitda_v <= 0:
        return MetricValue(
            name="net_debt_to_ebitda",
            value=None,
            status=MetricStatus.NA_UNINTERPRETABLE_RATIO,
            window=window,
            formula_id="net_debt",
            formula_version=version,
            notes="EBITDA <= 0 while net debt is positive; leverage is uninterpretable and requires review",
            inputs={"net_debt": nd, "EBITDA": ebitda_v},
            flags=["LEVERAGE_UNINTERPRETABLE"],
        )
    return MetricValue(
        name="net_debt_to_ebitda",
        value=nd / ebitda_v,
        status=MetricStatus.OK,
        window=window,
        formula_id="net_debt",
        formula_version=version,
        inputs={"net_debt": nd, "EBITDA": ebitda_v},
    )


def interest_coverage(ebit: float | None, interest: float | None, config: ScreenConfig, *, window: str) -> MetricValue:
    version = config.formula_version("interest_coverage")
    near_zero = float(config.require("quality_gates.interest_expense_near_zero"))
    if not is_finite(ebit) or not is_finite(interest):
        return MetricValue(
            name="interest_coverage",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="interest_coverage",
            formula_version=version,
            notes="Interest coverage is missing (not zero and not infinite) when the interest field is absent",
            inputs={"EBIT_adjusted": ebit, "interest_expense_gross": interest},
        )
    if abs(interest) <= near_zero:  # type: ignore[arg-type]
        return MetricValue(
            name="interest_coverage",
            value=None,
            status=MetricStatus.NA_NO_INTEREST,
            window=window,
            formula_id="interest_coverage",
            formula_version=version,
            notes="Interest expense is zero or near zero; coverage is NA_NO_INTEREST, not an infinite score",
            inputs={"EBIT_adjusted": ebit, "interest_expense_gross": interest},
            flags=["NA_NO_INTEREST"],
        )
    return MetricValue(
        name="interest_coverage",
        value=ebit / interest,  # type: ignore[operator]
        status=MetricStatus.OK,
        window=window,
        formula_id="interest_coverage",
        formula_version=version,
        inputs={"EBIT_adjusted": ebit, "interest_expense_gross": interest},
    )


def cash_short_debt_cover(cash: float | None, short_debt: float | None, config: ScreenConfig, *, window: str) -> MetricValue:
    if not is_finite(cash) or not is_finite(short_debt):
        return MetricValue(name="cash_short_debt_cover", value=None, status=MetricStatus.MISSING, window=window)
    if short_debt is None or short_debt <= 0:
        # No short-term interest-bearing debt: coverage is not infinite; caller scores via net cash / completeness.
        return MetricValue(
            name="cash_short_debt_cover",
            value=None,
            status=MetricStatus.NA_NO_INTEREST if short_debt == 0 else MetricStatus.INVALID,
            window=window,
            notes="No short-term interest-bearing debt; coverage is not treated as infinite",
            inputs={"unrestricted_cash": cash, "short_term_debt": short_debt},
            flags=["NO_SHORT_DEBT"],
        )
    return MetricValue(
        name="cash_short_debt_cover",
        value=cash / short_debt,
        status=MetricStatus.OK,
        window=window,
        inputs={"unrestricted_cash": cash, "short_term_debt": short_debt},
    )


def fcff(
    nopat_v: float | None,
    da: float | None,
    capex_v: float | None,
    delta_nwc: float | None,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    version = config.formula_version("FCFF")
    parts = {"NOPAT": nopat_v, "depreciation_amortization": da, "capex": capex_v, "delta_nwc": delta_nwc}
    if not all(is_finite(v) for v in parts.values()):
        return MetricValue(
            name="FCFF",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="FCFF",
            formula_version=version,
            inputs=parts,
            flags=["VALUATION_INPUT_INCOMPLETE"] if any(is_finite(v) for v in parts.values()) else [],
        )
    value = nopat_v + da - capex_v - delta_nwc  # type: ignore[operator]
    return MetricValue(
        name="FCFF",
        value=value,
        status=MetricStatus.OK,
        window=window,
        formula_id="FCFF",
        formula_version=version,
        inputs=parts,
    )
