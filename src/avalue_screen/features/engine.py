"""Orchestrate feature computation from a PIT period panel and market data.

This module only computes metrics. It does not apply hard gates or scores.
Missing values stay missing — they are never filled with zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import MetricStatus
from avalue_screen.domain.models import CompanyInput, MetricValue, PriceBar
from avalue_screen.features import balance_sheet as bs
from avalue_screen.features import cashflow as cf
from avalue_screen.features import per_share as ps
from avalue_screen.features import profitability as pf
from avalue_screen.pit.period_builder import AnnualPoint, PeriodPanel, QuarterPoint
from avalue_screen.utils import (
    TradingCalendar,
    is_finite,
    mean,
    median,
    ols_slope,
    stdev,
    winsorize,
    year_fraction,
)


@dataclass
class YearMetrics:
    fiscal_year: int
    report_period: date
    fields: dict[str, float | None]
    metrics: dict[str, MetricValue]


@dataclass
class FeatureSet:
    security_id: str
    as_of_date: date
    annual: list[YearMetrics]
    ttm: dict[str, MetricValue]
    ttm_period: date | None
    windows: dict[str, Any]
    market: dict[str, MetricValue]
    series: dict[str, list[float | None]]
    flags: list[str]
    notes: list[str]
    raw_annual_fields: list[dict[str, float | None]]

    def metric(self, name: str, default: Any = None) -> Any:
        if name in self.ttm and self.ttm[name].value is not None:
            return self.ttm[name].value
        return self.windows.get(name, default)

    def mv(self, name: str) -> MetricValue | None:
        if name in self.ttm:
            return self.ttm[name]
        return None


def _point_fields(point: AnnualPoint | QuarterPoint) -> dict[str, float | None]:
    return dict(point.values)


def _prior_annual(annuals: list[AnnualPoint], year: int) -> AnnualPoint | None:
    for a in annuals:
        if a.fiscal_year == year - 1:
            return a
    return None


def compute_features(
    company: CompanyInput,
    panel: PeriodPanel,
    as_of: date,
    config: ScreenConfig,
    *,
    calendar: TradingCalendar | None = None,
) -> FeatureSet:
    flags: list[str] = []
    notes: list[str] = []
    tax_info = _normalized_tax(panel, config)
    annual_metrics: list[YearMetrics] = []

    for ann in panel.annuals:
        fields = _point_fields(ann)
        prior = _prior_annual(panel.annuals, ann.fiscal_year)
        prior_fields = _point_fields(prior) if prior else {}
        metrics = _compute_period_metrics(
            fields,
            prior_fields,
            config,
            window=f"FY{ann.fiscal_year}",
            tax_rate=tax_info.value,
            flow_is_period=True,
        )
        annual_metrics.append(
            YearMetrics(
                fiscal_year=ann.fiscal_year,
                report_period=ann.report_period,
                fields=fields,
                metrics=metrics,
            )
        )

    ttm_metrics: dict[str, MetricValue] = {}
    ttm_period = None
    if panel.ttm:
        latest = panel.ttm[-1]
        ttm_period = latest.report_period
        # Prior TTM for averages: the TTM four quarters earlier, else prior annual / prior quarter stocks
        prior_stock = _prior_stock_for_ttm(panel, latest)
        ttm_metrics = _compute_period_metrics(
            _point_fields(latest),
            prior_stock,
            config,
            window="TTM",
            tax_rate=tax_info.value,
            flow_is_period=True,
        )
        ttm_metrics["normalized_tax_rate"] = tax_info

    windows = _window_stats(annual_metrics, ttm_metrics, config, flags)
    market = _market_metrics(company, as_of, ttm_metrics, config, calendar, flags, notes)
    series = _collect_series(annual_metrics)

    if tax_info.status == MetricStatus.TAX_RATE_FALLBACK:
        flags.append("TAX_RATE_FALLBACK")

    return FeatureSet(
        security_id=company.security_id,
        as_of_date=as_of,
        annual=annual_metrics,
        ttm=ttm_metrics,
        ttm_period=ttm_period,
        windows=windows,
        market=market,
        series=series,
        flags=flags,
        notes=notes,
        raw_annual_fields=[ym.fields for ym in annual_metrics],
    )


def _normalized_tax(panel: PeriodPanel, config: ScreenConfig) -> MetricValue:
    etrs: list[float] = []
    for ann in panel.annuals[-5:]:
        mv = pf.effective_tax_rate(
            ann.values.get("income_tax_expense"),
            ann.values.get("profit_before_tax"),
            config,
            window=f"FY{ann.fiscal_year}",
        )
        if mv.status == MetricStatus.OK and mv.value is not None:
            etrs.append(mv.value)
    return pf.normalized_tax_rate(etrs, config)


def _prior_stock_for_ttm(panel: PeriodPanel, latest: QuarterPoint) -> dict[str, float | None]:
    # Find the quarter one year earlier, or the previous annual / previous quarter.
    target_year, target_q = latest.fiscal_year, latest.quarter
    # Previous quarter for average stocks is the immediately preceding quarter.
    prev = None
    for q in panel.quarters:
        if (q.fiscal_year, q.quarter) == (target_year - 1, target_q) or (
            q.fiscal_year == target_year and q.quarter == target_q - 1
        ):
            prev = q
    # Prefer same-quarter prior year for YoY-like averages of annual-style ROIC;
    # for average IC we want t and t-1 (prior year-end or prior quarter).
    # Use the latest quarter strictly before current.
    earlier = [q for q in panel.quarters if q.report_period < latest.report_period]
    if earlier:
        return _point_fields(earlier[-1])
    return {}


def _compute_period_metrics(
    fields: dict[str, float | None],
    prior_fields: dict[str, float | None],
    config: ScreenConfig,
    *,
    window: str,
    tax_rate: float | None,
    flow_is_period: bool,
) -> dict[str, MetricValue]:
    out: dict[str, MetricValue] = {}
    ebit = pf.ebit_adjusted(fields, config, window=window)
    out["EBIT_adjusted"] = ebit
    out["NOPAT"] = pf.nopat(ebit.value, tax_rate, config, window=window)

    cash = bs.unrestricted_cash(fields, config, window=window)
    debt = bs.interest_bearing_debt(fields, config, window=window)
    short_debt = bs.short_term_interest_bearing_debt(fields, config, window=window)
    out["unrestricted_cash"] = cash
    out["interest_bearing_debt"] = debt
    out["interest_bearing_debt_due_within_12m"] = short_debt
    out["invested_capital"] = bs.invested_capital(
        fields.get("total_equity"), debt.value, cash.value, config, window=window
    )
    prior_cash = bs.unrestricted_cash(prior_fields, config, window=f"{window}-1") if prior_fields else None
    prior_debt = bs.interest_bearing_debt(prior_fields, config, window=f"{window}-1") if prior_fields else None
    prior_ic = None
    if prior_fields:
        prior_ic = bs.invested_capital(
            prior_fields.get("total_equity"),
            prior_debt.value if prior_debt else None,
            prior_cash.value if prior_cash else None,
            config,
            window=f"{window}-1",
        )
    out["ROIC"] = pf.roic_from_parts(
        out["NOPAT"].value,
        out["invested_capital"].value,
        prior_ic.value if prior_ic else None,
        config,
        window=window,
    )
    out["ROE"] = pf.roe_from_parts(
        fields.get("net_income_parent"),
        fields.get("equity_parent"),
        prior_fields.get("equity_parent") if prior_fields else None,
        config,
        window=window,
    )

    out["gross_margin"] = pf.margin(
        (fields["revenue"] - fields["cost_of_revenue"])
        if is_finite(fields.get("revenue")) and is_finite(fields.get("cost_of_revenue"))
        else None,
        fields.get("revenue"),
        "gross_margin",
        config,
        window=window,
    )
    out["operating_margin"] = pf.margin(ebit.value, fields.get("revenue"), "operating_margin", config, window=window)
    out["selling_expense_ratio"] = pf.margin(fields.get("selling_expense"), fields.get("revenue"), "selling_expense_ratio", config, window=window)
    out["admin_expense_ratio"] = pf.margin(fields.get("admin_expense"), fields.get("revenue"), "admin_expense_ratio", config, window=window)
    out["rd_expense_ratio"] = pf.margin(fields.get("rd_expense"), fields.get("revenue"), "rd_expense_ratio", config, window=window)

    out["EBITDA"] = cf.ebitda(ebit.value, fields.get("depreciation_amortization"), config, window=window)
    out["net_debt"] = cf.net_debt(debt.value, cash.value, config, window=window)
    out["net_debt_to_ebitda"] = cf.net_debt_to_ebitda(out["net_debt"].value, out["EBITDA"].value, config, window=window)
    out["interest_coverage"] = cf.interest_coverage(ebit.value, fields.get("interest_expense_gross"), config, window=window)
    out["cash_short_debt_cover"] = cf.cash_short_debt_cover(cash.value, short_debt.value, config, window=window)

    capex_mv = cf.capex(fields.get("cash_paid_for_capex_ppe"), config, window=window)
    out["capex"] = capex_mv
    out["FCF"] = cf.fcf(fields.get("cash_flow_operating"), capex_mv.value, config, window=window)

    recv = bs.trade_receivables(fields, config, window=window)
    out["trade_receivables"] = recv
    notes_payable = fields.get("notes_payable")
    # Default: notes payable are operating (non-interest) unless mapping says otherwise.
    notes_noninterest = notes_payable
    nwc = bs.operating_nwc(
        recv.value,
        fields.get("inventory"),
        fields.get("accounts_payable"),
        notes_noninterest,
        fields.get("contract_liabilities"),
        config,
        window=window,
    )
    out["operating_nwc"] = nwc
    prior_recv = bs.trade_receivables(prior_fields, config, window=f"{window}-1") if prior_fields else None
    prior_nwc = (
        bs.operating_nwc(
            prior_recv.value if prior_recv else None,
            prior_fields.get("inventory"),
            prior_fields.get("accounts_payable"),
            prior_fields.get("notes_payable"),
            prior_fields.get("contract_liabilities"),
            config,
            window=f"{window}-1",
        )
        if prior_fields
        else None
    )
    delta = None
    delta_status = MetricStatus.MISSING
    if nwc.value is not None and prior_nwc is not None and prior_nwc.value is not None:
        delta = nwc.value - prior_nwc.value
        delta_status = MetricStatus.OK
    out["delta_nwc"] = MetricValue(
        name="delta_nwc",
        value=delta,
        status=delta_status,
        window=window,
        formula_id="operating_nwc",
        formula_version=config.formula_version("operating_nwc"),
        inputs={"operating_nwc_t": nwc.value, "operating_nwc_t-1": prior_nwc.value if prior_nwc else None},
    )

    days = 365.0
    avg_recv = _avg(recv.value, prior_recv.value if prior_recv else None)
    avg_inv = _avg(fields.get("inventory"), prior_fields.get("inventory") if prior_fields else None)
    out["receivable_days"] = bs.turnover_days(avg_recv, fields.get("revenue"), days, "receivable_days", config, window=window)
    out["inventory_days"] = bs.turnover_days(avg_inv, fields.get("cost_of_revenue"), days, "inventory_days", config, window=window)

    out["FCFF"] = cf.fcff(
        out["NOPAT"].value,
        fields.get("depreciation_amortization"),
        capex_mv.value,
        out["delta_nwc"].value,
        config,
        window=window,
    )

    # Pass through key raw fields as metrics for explainability (value may be None).
    for raw_name in (
        "revenue",
        "net_income",
        "net_income_parent",
        "net_income_ex_nonrecurring_parent",
        "nonrecurring_profit_parent",
        "cash_flow_operating",
        "total_equity",
        "equity_parent",
        "total_assets",
        "total_liabilities",
        "goodwill",
        "depreciation_amortization",
        "interest_expense_gross",
        "profit_before_tax",
        "income_tax_expense",
        "cost_of_revenue",
        "inventory",
        "accounts_payable",
    ):
        val = fields.get(raw_name)
        out[raw_name] = MetricValue(
            name=raw_name,
            value=val,
            status=MetricStatus.OK if is_finite(val) else MetricStatus.MISSING,
            window=window,
        )
    return out


def _avg(a: float | None, b: float | None) -> float | None:
    if is_finite(a) and is_finite(b):
        return (a + b) / 2.0  # type: ignore[operator]
    if is_finite(a):
        return a
    return None


def _series(annual: list[YearMetrics], name: str, n: int | None = None) -> list[float | None]:
    rows = annual[-n:] if n else annual
    out: list[float | None] = []
    for ym in rows:
        mv = ym.metrics.get(name)
        out.append(mv.value if mv is not None else ym.fields.get(name))
    return out


def _valid_series(vals: list[float | None]) -> list[float]:
    return [v for v in vals if is_finite(v)]


def _window_stats(
    annual: list[YearMetrics],
    ttm: dict[str, MetricValue],
    config: ScreenConfig,
    flags: list[str],
) -> dict[str, Any]:
    last5 = annual[-5:]
    last3 = annual[-3:]
    out: dict[str, Any] = {
        "annual_years": [ym.fiscal_year for ym in annual],
        "n_annual_years": len(annual),
        "n_complete_years_5": len(last5),
        "n_complete_years_3": len(last3),
    }

    def summarize(name: str, years: list[YearMetrics], prefix: str) -> None:
        vals = _series(years, name)
        valid = _valid_series(vals)
        out[f"{prefix}_{name}_values"] = vals
        out[f"{prefix}_{name}_median"] = median(valid)
        out[f"{prefix}_{name}_mean"] = mean(valid)
        out[f"{prefix}_{name}_std"] = stdev(valid)
        out[f"{prefix}_{name}_positive_years"] = sum(1 for v in valid if v > 0)
        out[f"{prefix}_{name}_valid_years"] = len(valid)
        out[f"{prefix}_{name}_slope"] = ols_slope(vals)
        out[f"{prefix}_{name}_sum"] = sum(valid) if valid else None
        if valid:
            lo = float(config.require("normalization.winsorize_lower_percentile"))
            hi = float(config.require("normalization.winsorize_upper_percentile"))
            # Auxiliary only — raw values are retained above.
            out[f"{prefix}_{name}_winsorized"] = winsorize(valid, lo, hi)

    for name in (
        "ROIC",
        "ROE",
        "gross_margin",
        "operating_margin",
        "revenue",
        "net_income",
        "net_income_parent",
        "net_income_ex_nonrecurring_parent",
        "nonrecurring_profit_parent",
        "cash_flow_operating",
        "FCF",
        "FCFF",
        "EBIT_adjusted",
        "EBITDA",
        "NOPAT",
        "capex",
        "operating_nwc",
        "interest_coverage",
        "net_debt",
        "net_debt_to_ebitda",
        "receivable_days",
        "inventory_days",
        "trade_receivables",
        "inventory",
        "depreciation_amortization",
        "equity_parent",
    ):
        summarize(name, last5, "y5")
        summarize(name, last3, "y3")
        summarize(name, last5[-2:] if last5 else [], "y2")

    # Conversion ratios over 5 complete years (and 3y for observation).
    cfo_5 = out.get("y5_cash_flow_operating_sum")
    ni_5 = out.get("y5_net_income_sum")
    fcf_5 = out.get("y5_FCF_sum")
    adj_5 = out.get("y5_net_income_ex_nonrecurring_parent_sum")
    out["cash_conversion_5y"] = cf.conversion_ratio(
        cfo_5, ni_5, "cash_conversion_5y", "cash_conversion_5y", config, window="5y"
    )
    out["fcf_conversion_5y"] = cf.conversion_ratio(
        fcf_5, adj_5, "fcf_conversion_5y", "fcf_conversion_5y", config, window="5y"
    )

    # Nonrecurring reliance: sum(nonrecurring) / sum(NI parent)
    nr = out.get("y5_nonrecurring_profit_parent_sum")
    ni_p = out.get("y5_net_income_parent_sum")
    out["nonrecurring_reliance_5y"] = cf.conversion_ratio(
        nr, ni_p, "nonrecurring_reliance_5y", "cash_conversion_5y", config, window="5y"
    )
    # If NI parent crosses zero / tiny, conversion_ratio already marks uninterpretable.

    # CAGRs
    def book_cagr(name: str, years: int, key: str) -> None:
        series = _series(annual[-years:], name) if annual else []
        if len(series) >= 2:
            out[key] = ps.cagr_metric(series[0], series[-1], float(years - 0), name=key)
            # For n-year CAGR we need n periods of growth = years_span.
            # If we have 5 annual points, span is 4 years. Adjust.
            span = len(series) - 1
            out[key] = ps.cagr_metric(series[0], series[-1], float(span), name=key)
        else:
            out[key] = ps.cagr_metric(None, None, float(years), name=key)

    book_cagr("revenue", 5, "revenue_cagr_5y")
    book_cagr("revenue", 3, "revenue_cagr_3y")
    book_cagr("net_income_ex_nonrecurring_parent", 5, "adj_np_cagr_5y")
    book_cagr("net_income_ex_nonrecurring_parent", 3, "adj_np_cagr_3y")
    book_cagr("FCF", 5, "fcf_cagr_5y")
    book_cagr("FCF", 3, "fcf_cagr_3y")

    # YoY on last two annuals / TTM vs TTM-4 handled in market/ttm later if needed
    if len(annual) >= 2:
        a, b = annual[-2], annual[-1]
        out["revenue_growth_yoy"] = ps.yoy_growth(b.metrics["revenue"].value, a.metrics["revenue"].value, "revenue_growth_yoy", window="FY")
        out["adjusted_np_growth_yoy"] = ps.yoy_growth(
            b.metrics["net_income_ex_nonrecurring_parent"].value,
            a.metrics["net_income_ex_nonrecurring_parent"].value,
            "adjusted_np_growth_yoy",
            window="FY",
        )

    # Receivable / inventory growth vs revenue growth (last two year-on-year steps)
    out["recv_growth_gap_years"] = _growth_gap_flags(annual, "trade_receivables", "revenue", config)
    out["inv_growth_gap_years"] = _growth_gap_flags(annual, "inventory", "revenue", config)

    # Dilution / EPS path is computed when share counts are available in market metrics.
    out["roic_3y_decline"] = _roic_decline(last5)

    # Mid-cycle (median) earnings for cyclical handling
    out["midcycle_EBIT"] = out.get("y5_EBIT_adjusted_median")
    out["midcycle_net_income_parent"] = out.get("y5_net_income_parent_median")
    out["midcycle_FCF"] = out.get("y5_FCF_median")
    out["midcycle_NOPAT"] = out.get("y5_NOPAT_median")
    out["midcycle_FCFF"] = out.get("y5_FCFF_median")

    # Ratios used by DCF defaults
    out["da_to_revenue_median"] = _ratio_median(last5, "depreciation_amortization", "revenue")
    out["capex_to_revenue_median"] = _ratio_median(last5, "capex", "revenue")
    out["nwc_to_revenue_median"] = _ratio_median(last5, "operating_nwc", "revenue")

    return out


def _ratio_median(years: list[YearMetrics], numer: str, denom: str) -> float | None:
    ratios: list[float] = []
    for ym in years:
        n = ym.metrics.get(numer)
        d = ym.metrics.get(denom)
        nv = n.value if n else ym.fields.get(numer)
        dv = d.value if d else ym.fields.get(denom)
        if is_finite(nv) and is_finite(dv) and dv not in (0, None):
            ratios.append(nv / dv)  # type: ignore[operator]
    return median(ratios)


def _growth_gap_flags(
    annual: list[YearMetrics], stock_name: str, revenue_name: str, config: ScreenConfig
) -> list[dict[str, Any]]:
    gap = float(config.require("quality_gates.receivable_inventory_growth_gap"))
    rows: list[dict[str, Any]] = []
    for a, b in zip(annual, annual[1:]):
        s0 = a.metrics.get(stock_name)
        s1 = b.metrics.get(stock_name)
        r0 = a.metrics.get(revenue_name)
        r1 = b.metrics.get(revenue_name)
        sv0 = s0.value if s0 else a.fields.get(stock_name)
        sv1 = s1.value if s1 else b.fields.get(stock_name)
        rv0 = r0.value if r0 else a.fields.get(revenue_name)
        rv1 = r1.value if r1 else b.fields.get(revenue_name)
        if not all(is_finite(x) for x in (sv0, sv1, rv0, rv1)) or rv0 in (0, None) or sv0 in (0, None):
            continue
        stock_g = sv1 / sv0 - 1.0  # type: ignore[operator]
        rev_g = rv1 / rv0 - 1.0  # type: ignore[operator]
        rows.append(
            {
                "year": b.fiscal_year,
                "stock_growth": stock_g,
                "revenue_growth": rev_g,
                "gap": stock_g - rev_g,
                "exceeds": (stock_g - rev_g) > gap,
            }
        )
    return rows


def _roic_decline(last5: list[YearMetrics]) -> dict[str, Any]:
    roics = []
    for ym in last5:
        mv = ym.metrics.get("ROIC")
        roics.append(mv.value if mv and mv.is_valid else None)
    last3 = roics[-3:] if len(roics) >= 3 else roics
    decline = None
    if len(last3) >= 2 and is_finite(last3[0]) and is_finite(last3[-1]):
        decline = last3[0] - last3[-1]  # type: ignore[operator]
    continuous = all(is_finite(a) and is_finite(b) and b < a for a, b in zip(roics, roics[1:])) if len(roics) >= 3 else False
    return {"values": roics, "last3_decline": decline, "continuous_decline": continuous}


def _collect_series(annual: list[YearMetrics]) -> dict[str, list[float | None]]:
    names = [
        "ROIC",
        "ROE",
        "gross_margin",
        "operating_margin",
        "revenue",
        "net_income_parent",
        "net_income_ex_nonrecurring_parent",
        "FCF",
        "FCFF",
        "cash_flow_operating",
        "EBIT_adjusted",
        "interest_coverage",
        "net_debt",
        "trade_receivables",
        "inventory",
        "EPS_adjusted",
    ]
    return {name: _series(annual, name) for name in names}


def _bars_on_or_before(prices: list[PriceBar], as_of: date) -> list[PriceBar]:
    return sorted([p for p in prices if p.price_date <= as_of], key=lambda p: p.price_date)


def _bar_on_or_before(prices: list[PriceBar], as_of: date) -> PriceBar | None:
    bars = _bars_on_or_before(prices, as_of)
    return bars[-1] if bars else None


def _market_metrics(
    company: CompanyInput,
    as_of: date,
    ttm: dict[str, MetricValue],
    config: ScreenConfig,
    calendar: TradingCalendar | None,
    flags: list[str],
    notes: list[str],
) -> dict[str, MetricValue]:
    out: dict[str, MetricValue] = {}
    prices = company.market.prices
    bar = _bar_on_or_before(prices, as_of)
    close_u = bar.close_unadjusted if bar else None
    close_a = bar.close_adjusted if bar else None
    tri = bar.total_return_index if bar else None
    shares = bar.total_shares if bar else None
    diluted = bar.diluted_shares if bar else None
    provided_mcap = bar.market_cap if bar else None
    turnover = bar.daily_turnover_value if bar else None

    out["close_unadjusted"] = MetricValue(name="close_unadjusted", value=close_u, status=MetricStatus.OK if is_finite(close_u) else MetricStatus.MISSING, window=str(as_of))
    out["close_adjusted"] = MetricValue(name="close_adjusted", value=close_a, status=MetricStatus.OK if is_finite(close_a) else MetricStatus.MISSING, window=str(as_of))
    out["total_shares"] = MetricValue(name="total_shares", value=shares, status=MetricStatus.OK if is_finite(shares) else MetricStatus.MISSING, window=str(as_of))
    out["diluted_shares"] = MetricValue(
        name="diluted_shares",
        value=diluted if is_finite(diluted) else shares,
        status=MetricStatus.SHARES_FALLBACK if not is_finite(diluted) and is_finite(shares) else (MetricStatus.OK if is_finite(diluted) else MetricStatus.MISSING),
        window=str(as_of),
        flags=["SHARES_FALLBACK"] if not is_finite(diluted) and is_finite(shares) else [],
    )
    out["market_cap"] = ps.market_cap(close_u, shares, provided_mcap, config, window=str(as_of))
    ev = ps.enterprise_value(
        out["market_cap"].value,
        ttm.get("interest_bearing_debt").value if ttm.get("interest_bearing_debt") else None,
        ttm.get("unrestricted_cash").value if ttm.get("unrestricted_cash") else None,
        None,
        config,
        window=str(as_of),
    )
    out["enterprise_value"] = ev

    # Liquidity: median turnover over lookback trading days
    lookback = int(config.get("universe.liquidity_lookback_days", 20))
    recent = _bars_on_or_before(prices, as_of)[-lookback:]
    turns = [p.daily_turnover_value for p in recent if is_finite(p.daily_turnover_value)]
    out["daily_turnover_cny"] = MetricValue(
        name="daily_turnover_cny",
        value=median(turns) if turns else turnover,
        status=MetricStatus.OK if (turns or is_finite(turnover)) else MetricStatus.MISSING,
        window=f"{lookback}d",
    )

    # 12-month total return
    from datetime import timedelta

    start_target = as_of - timedelta(days=365)
    start_bar = _bar_on_or_before(prices, start_target)
    # Prefer next bar on or after start_target if exact miss — use first bar with date >= start and last <= as_of
    bars = _bars_on_or_before(prices, as_of)
    start_or_after = [p for p in bars if p.price_date >= start_target]
    start_bar = start_or_after[0] if start_or_after else start_bar
    ret = None
    status = MetricStatus.MISSING
    if bar and start_bar:
        if is_finite(bar.total_return_index) and is_finite(start_bar.total_return_index) and start_bar.total_return_index not in (0, None):
            ret = bar.total_return_index / start_bar.total_return_index - 1.0
            status = MetricStatus.OK
        elif is_finite(bar.close_adjusted) and is_finite(start_bar.close_adjusted) and start_bar.close_adjusted not in (0, None):
            ret = bar.close_adjusted / start_bar.close_adjusted - 1.0
            status = MetricStatus.OK
            flags.append("TOTAL_RETURN_USED_ADJUSTED_PRICE")
    out["price_12m_total_return"] = MetricValue(
        name="price_12m_total_return",
        value=ret,
        status=status,
        window="12m",
        inputs={
            "start_date": start_bar.price_date.isoformat() if start_bar else None,
            "end_date": bar.price_date.isoformat() if bar else None,
        },
    )

    # 3-year market-cap drawdown from peak
    start_3y = as_of - timedelta(days=int(3 * float(config.get("screening.days_per_year", 365.25))))
    window_bars = [p for p in bars if p.price_date >= start_3y]
    peak = None
    for p in window_bars:
        cap = p.market_cap
        if cap is None and is_finite(p.close_unadjusted) and is_finite(p.total_shares):
            cap = p.close_unadjusted * p.total_shares
        if is_finite(cap):
            peak = cap if peak is None else max(peak, cap)
    current_cap = out["market_cap"].value
    dd = None
    st = MetricStatus.MISSING
    if is_finite(peak) and is_finite(current_cap) and peak not in (0, None):
        dd = 1.0 - current_cap / peak  # type: ignore[operator]
        st = MetricStatus.OK
    out["market_cap_drawdown_3y"] = MetricValue(
        name="market_cap_drawdown_3y",
        value=dd,
        status=st,
        window="3y",
        inputs={"peak_market_cap": peak, "current_market_cap": current_cap},
    )

    # Share count history for dilution (from price bars or share events)
    share_path = [
        (p.price_date, p.total_shares)
        for p in bars
        if is_finite(p.total_shares)
    ]
    out["_share_path"] = MetricValue(
        name="_share_path",
        value=float(len(share_path)),
        status=MetricStatus.OK,
        window="history",
        inputs={"points": [(d.isoformat(), s) for d, s in share_path[-12:]]},
    )
    # 3-year dilution: shares now vs shares ~3y ago
    dil = None
    shares_then = None
    if share_path:
        then_candidates = [s for d, s in share_path if d <= start_3y]
        shares_then = then_candidates[-1] if then_candidates else share_path[0][1]
        shares_now = share_path[-1][1]
        if is_finite(shares_then) and is_finite(shares_now) and shares_then not in (0, None):
            dil = shares_now / shares_then - 1.0
    out["dilution_3y"] = MetricValue(
        name="dilution_3y",
        value=dil,
        status=MetricStatus.OK if is_finite(dil) else MetricStatus.MISSING,
        window="3y",
        inputs={"shares_then": shares_then, "shares_now": shares},
    )

    # EPS TTM
    out["EPS_adjusted"] = ps.eps_adjusted(
        ttm.get("net_income_ex_nonrecurring_parent").value if ttm.get("net_income_ex_nonrecurring_parent") else None,
        diluted,
        shares,
        config,
        window="TTM",
    )

    # Listing tenure
    listing = company.market.listing_date
    tenure = year_fraction(listing, as_of, float(config.get("screening.listing_year_basis", 365.25))) if listing else None
    out["listing_years"] = MetricValue(
        name="listing_years",
        value=tenure,
        status=MetricStatus.OK if is_finite(tenure) else MetricStatus.MISSING,
        window=str(as_of),
        inputs={"listing_date": listing.isoformat() if listing else None},
    )

    if bar and bar.suspension_flag:
        flags.append("SUSPENDED_ON_AS_OF")
    return out
