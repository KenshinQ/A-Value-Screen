"""Balance-sheet formulas: debt, cash, invested capital, working capital."""

from __future__ import annotations

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import MetricStatus
from avalue_screen.domain.models import MetricValue
from avalue_screen.utils import is_finite

DEBT_COMPONENTS = (
    "short_term_borrowings",
    "current_portion_long_term_debt",
    "long_term_borrowings",
    "bonds_payable",
    "lease_liabilities_current",
    "lease_liabilities_noncurrent",
    "other_explicit_interest_bearing_debt",
)

SHORT_DEBT_COMPONENTS = (
    "short_term_borrowings",
    "current_portion_long_term_debt",
    "lease_liabilities_current",
)

RECEIVABLE_COMPONENTS = (
    "accounts_receivable",
    "notes_receivable",
    "accounts_receivable_financing",
    "contract_assets",
)


def _sum_optional(fields: dict[str, float | None], names: tuple[str, ...], *, include_lease: bool = True) -> tuple[float | None, list[str], bool]:
    total = 0.0
    present = False
    used: list[str] = []
    for name in names:
        if name.startswith("lease_") and not include_lease:
            continue
        val = fields.get(name)
        if val is None:
            continue
        present = True
        total += val
        used.append(name)
    if not present:
        return None, used, False
    return total, used, True


def unrestricted_cash(fields: dict[str, float | None], config: ScreenConfig, *, window: str) -> MetricValue:
    cash = fields.get("cash_and_equivalents")
    restricted = fields.get("restricted_cash") or 0.0 if fields.get("cash_and_equivalents") is not None else None
    if not is_finite(cash):
        return MetricValue(
            name="unrestricted_cash_and_equivalents",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="invested_capital",
            formula_version=config.formula_version("invested_capital"),
        )
    restricted = fields.get("restricted_cash") or 0.0
    value = max(0.0, cash - restricted)  # type: ignore[operator]
    flags: list[str] = []
    if bool(config.get("normalization.deduct_trading_financial_assets_from_cash", False)):
        trad = fields.get("trading_financial_assets") or 0.0
        value = max(0.0, value - trad)
        flags.append("TRADING_ASSETS_DEDUCTED")
    return MetricValue(
        name="unrestricted_cash_and_equivalents",
        value=value,
        status=MetricStatus.OK,
        window=window,
        formula_id="invested_capital",
        formula_version=config.formula_version("invested_capital"),
        inputs={"cash_and_equivalents": cash, "restricted_cash": fields.get("restricted_cash")},
        flags=flags,
    )


def interest_bearing_debt(fields: dict[str, float | None], config: ScreenConfig, *, window: str) -> MetricValue:
    include_lease = bool(config.get("normalization.lease_liabilities_in_debt", True))
    total, used, present = _sum_optional(fields, DEBT_COMPONENTS, include_lease=include_lease)
    if not present:
        # Distinguish "explicitly all zero / missing components" — if none of the
        # components are present, debt is missing, not zero.
        return MetricValue(
            name="interest_bearing_debt",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="invested_capital",
            formula_version=config.formula_version("invested_capital"),
            notes="No interest-bearing debt components were provided",
        )
    return MetricValue(
        name="interest_bearing_debt",
        value=total,
        status=MetricStatus.OK,
        window=window,
        formula_id="invested_capital",
        formula_version=config.formula_version("invested_capital"),
        inputs={"components": used, "include_lease": include_lease},
    )


def short_term_interest_bearing_debt(fields: dict[str, float | None], config: ScreenConfig, *, window: str) -> MetricValue:
    include_lease = bool(config.get("normalization.lease_liabilities_in_debt", True))
    total, used, present = _sum_optional(fields, SHORT_DEBT_COMPONENTS, include_lease=include_lease)
    if not present:
        return MetricValue(
            name="interest_bearing_debt_due_within_12m",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
        )
    return MetricValue(
        name="interest_bearing_debt_due_within_12m",
        value=total,
        status=MetricStatus.OK,
        window=window,
        inputs={"components": used},
    )


def invested_capital(
    total_equity: float | None,
    debt: float | None,
    cash: float | None,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    version = config.formula_version("invested_capital")
    if not is_finite(total_equity) or not is_finite(debt) or not is_finite(cash):
        return MetricValue(
            name="invested_capital",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="invested_capital",
            formula_version=version,
            inputs={"total_equity": total_equity, "interest_bearing_debt": debt, "unrestricted_cash": cash},
        )
    return MetricValue(
        name="invested_capital",
        value=total_equity + debt - cash,  # type: ignore[operator]
        status=MetricStatus.OK,
        window=window,
        formula_id="invested_capital",
        formula_version=version,
        inputs={"total_equity": total_equity, "interest_bearing_debt": debt, "unrestricted_cash": cash},
    )


def trade_receivables(fields: dict[str, float | None], config: ScreenConfig, *, window: str) -> MetricValue:
    total, used, present = _sum_optional(fields, RECEIVABLE_COMPONENTS)
    if not present:
        return MetricValue(
            name="trade_receivables",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="trade_receivables",
            formula_version=config.formula_version("trade_receivables"),
        )
    return MetricValue(
        name="trade_receivables",
        value=total,
        status=MetricStatus.OK,
        window=window,
        formula_id="trade_receivables",
        formula_version=config.formula_version("trade_receivables"),
        inputs={"components": used},
    )


def operating_nwc(
    trade_recv: float | None,
    inventory: float | None,
    accounts_payable: float | None,
    notes_payable_noninterest: float | None,
    contract_liabilities: float | None,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    # Inventory / payables may legitimately be zero; they must be present (including explicit 0).
    parts = {
        "trade_receivables": trade_recv,
        "inventory": inventory,
        "accounts_payable": accounts_payable,
        "notes_payable_noninterest_bearing": notes_payable_noninterest,
        "contract_liabilities": contract_liabilities,
    }
    # notes payable and contract liabilities may be absent (= 0 if the field was never used).
    if not is_finite(trade_recv) or not is_finite(inventory) or not is_finite(accounts_payable):
        return MetricValue(
            name="operating_nwc",
            value=None,
            status=MetricStatus.MISSING,
            window=window,
            formula_id="operating_nwc",
            formula_version=config.formula_version("operating_nwc"),
            inputs=parts,
        )
    npay = notes_payable_noninterest or 0.0
    cl = contract_liabilities or 0.0
    value = trade_recv + inventory - accounts_payable - npay - cl  # type: ignore[operator]
    return MetricValue(
        name="operating_nwc",
        value=value,
        status=MetricStatus.OK,
        window=window,
        formula_id="operating_nwc",
        formula_version=config.formula_version("operating_nwc"),
        inputs=parts,
    )


def turnover_days(
    average_stock: float | None,
    flow: float | None,
    days: float,
    name: str,
    config: ScreenConfig,
    *,
    window: str,
) -> MetricValue:
    if not is_finite(average_stock) or not is_finite(flow):
        return MetricValue(name=name, value=None, status=MetricStatus.MISSING, window=window, formula_id=name, formula_version=config.formula_version(name))
    if flow is None or flow <= 0:
        return MetricValue(
            name=name,
            value=None,
            status=MetricStatus.INVALID,
            window=window,
            formula_id=name,
            formula_version=config.formula_version(name),
            notes="Flow denominator <= 0; turnover days are invalid",
            inputs={"average_stock": average_stock, "flow": flow},
        )
    return MetricValue(
        name=name,
        value=average_stock / flow * days,
        status=MetricStatus.OK,
        window=window,
        formula_id=name,
        formula_version=config.formula_version(name),
        inputs={"average_stock": average_stock, "flow": flow, "days": days},
    )
