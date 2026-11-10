"""Build the v0.1 synthetic universe used by integration tests and example CLI runs."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SH = ZoneInfo("Asia/Shanghai")
AS_OF = date(2026, 11, 10)
YEARS = [2021, 2022, 2023, 2024, 2025]
# 2026 YTD quarters used to form TTM as of 2026-11-10


def _ann(d: date, hour: int = 18) -> str:
    return datetime(d.year, d.month, d.day, hour, 0, 0, tzinfo=SH).isoformat()


def _announce_for(period: date, stype: str) -> str:
    if stype == "Q1":
        return _ann(date(period.year, 4, 25))
    if stype == "H1":
        return _ann(date(period.year, 8, 25))
    if stype == "Q3":
        return _ann(date(period.year, 10, 28))
    # ANNUAL: next year March 31 after close
    return _ann(date(period.year + 1, 3, 31))


def _period(year: int, stype: str) -> date:
    return {
        "Q1": date(year, 3, 31),
        "H1": date(year, 6, 30),
        "Q3": date(year, 9, 30),
        "ANNUAL": date(year, 12, 31),
    }[stype]


def _split_flow(annual: dict[str, float], weights: tuple[float, float, float, float] = (0.24, 0.25, 0.25, 0.26)) -> list[dict[str, float]]:
    qs = []
    for w in weights:
        qs.append({k: v * w for k, v in annual.items()})
    return qs


def _cumsum(quarters: list[dict[str, float]], n: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for q in quarters[:n]:
        for k, v in q.items():
            out[k] = out.get(k, 0.0) + v
    return out


def _lerp_stock(start: dict[str, float], end: dict[str, float], t: float) -> dict[str, float]:
    keys = set(start) | set(end)
    out = {}
    for k in keys:
        a = start.get(k, end.get(k, 0.0))
        b = end.get(k, start.get(k, 0.0))
        out[k] = a + (b - a) * t
    return out


FLOW_FIELDS = {
    "revenue", "cost_of_revenue", "selling_expense", "admin_expense", "rd_expense",
    "finance_expense", "interest_expense_gross", "interest_income", "operating_profit",
    "non_operating_income_net", "profit_before_tax", "income_tax_expense", "net_income",
    "net_income_parent", "net_income_ex_nonrecurring_parent", "nonrecurring_profit_parent",
    "depreciation_amortization", "investment_income", "fair_value_change", "asset_disposal_gain",
    "cash_flow_operating", "cash_paid_for_capex_ppe", "cash_received_from_asset_disposal",
    "cash_paid_for_acquisitions", "cash_paid_for_interest", "cash_paid_for_taxes",
    "cash_flow_investing", "cash_flow_financing",
}

STOCK_FIELDS = {
    "cash_and_equivalents", "restricted_cash", "trading_financial_assets",
    "accounts_receivable", "notes_receivable", "accounts_receivable_financing", "contract_assets",
    "inventory", "accounts_payable", "notes_payable", "contract_liabilities",
    "total_assets", "total_liabilities", "total_equity", "equity_parent", "minority_interest",
    "short_term_borrowings", "current_portion_long_term_debt", "long_term_borrowings",
    "bonds_payable", "lease_liabilities_current", "lease_liabilities_noncurrent",
    "goodwill", "intangible_assets", "development_expenditure_capitalized",
}


def _fv(value: float | None, unit: str = "yuan") -> dict:
    return {"value": value, "unit": unit, "currency": "CNY", "source": "synthetic_fixture"}


def _pack(fields: dict[str, float]) -> tuple[dict, dict, dict]:
    income, balance, cashflow = {}, {}, {}
    cashflow_names = {
        "cash_flow_operating", "cash_paid_for_capex_ppe", "cash_received_from_asset_disposal",
        "cash_paid_for_acquisitions", "cash_paid_for_interest", "cash_paid_for_taxes",
        "cash_flow_investing", "cash_flow_financing",
    }
    for k, v in fields.items():
        item = _fv(v)
        if k in cashflow_names:
            cashflow[k] = item
        elif k in STOCK_FIELDS:
            balance[k] = item
        else:
            income[k] = item
    return income, balance, cashflow


def statements_from_years(
    security_id: str,
    symbol: str,
    exchange: str,
    years_data: dict[int, dict],
    *,
    version_id: str = "v1",
    version_chain: str = "v1",
    is_restatement: bool = False,
    include_2026_ytd: bool = True,
    ytd_2026: dict | None = None,
    announce_override: dict | None = None,
) -> list[dict]:
    """years_data[year] = {'flow': {...annual...}, 'stock_end': {...}, 'stock_start': {...} optional}."""
    stmts = []
    years = sorted(years_data)
    for i, year in enumerate(years):
        rec = years_data[year]
        flow = rec["flow"]
        stock_end = rec["stock_end"]
        prev_year = years[i - 1] if i else None
        stock_start = rec.get("stock_start") or (years_data[prev_year]["stock_end"] if prev_year else stock_end)
        q_flows = rec.get("quarter_weights")
        quarters_flow = _split_flow(flow, q_flows) if q_flows else _split_flow(flow)
        for stype, nq, t in (("Q1", 1, 0.25), ("H1", 2, 0.50), ("Q3", 3, 0.75), ("ANNUAL", 4, 1.0)):
            period = _period(year, stype)
            flow_cum = _cumsum(quarters_flow, nq)
            stock = _lerp_stock(stock_start, stock_end, t)
            fields = {**flow_cum, **stock}
            income, balance, cashflow = _pack(fields)
            key = (year, stype, version_id)
            ann = (announce_override or {}).get(key) or _announce_for(period, stype)
            stmts.append(
                {
                    "security_id": security_id,
                    "symbol": symbol,
                    "exchange": exchange,
                    "report_period": period.isoformat(),
                    "announcement_datetime": ann,
                    "statement_type": stype,
                    "version_id": version_id,
                    "version_chain": version_chain,
                    "is_restatement": is_restatement,
                    "source": "synthetic_fixture",
                    "currency": "CNY",
                    "income": income,
                    "balance": balance,
                    "cashflow": cashflow,
                }
            )
    if include_2026_ytd:
        base = ytd_2026 or years_data[years[-1]]
        flow = base["flow"]
        # modest growth in 2026 run-rate if not specified
        if ytd_2026 is None:
            flow = {k: v * 1.04 for k, v in flow.items()}
        stock_start = years_data[years[-1]]["stock_end"]
        stock_end = base.get("stock_end") or {k: v * 1.02 for k, v in stock_start.items()}
        quarters_flow = _split_flow(flow)
        for stype, nq, t in (("Q1", 1, 0.25), ("H1", 2, 0.50), ("Q3", 3, 0.75)):
            period = _period(2026, stype)
            fields = {**_cumsum(quarters_flow, nq), **_lerp_stock(stock_start, stock_end, t)}
            income, balance, cashflow = _pack(fields)
            stmts.append(
                {
                    "security_id": security_id,
                    "symbol": symbol,
                    "exchange": exchange,
                    "report_period": period.isoformat(),
                    "announcement_datetime": _announce_for(period, stype),
                    "statement_type": stype,
                    "version_id": version_id,
                    "version_chain": version_chain,
                    "is_restatement": is_restatement,
                    "source": "synthetic_fixture",
                    "currency": "CNY",
                    "income": income,
                    "balance": balance,
                    "cashflow": cashflow,
                }
            )
    return stmts


def _income_from_operating(
    *,
    revenue: float,
    gross_margin: float,
    ebit: float,
    interest_expense: float,
    interest_income: float = 0.0,
    tax_rate: float = 0.25,
    da: float = 0.0,
    selling: float | None = None,
    admin: float | None = None,
    rd: float = 0.0,
    nonrecurring: float = 0.0,
    minority_ni: float = 0.0,
) -> dict[str, float]:
    cogs = revenue * (1.0 - gross_margin)
    # EBIT = OP + interest_exp - interest_inc - nonop(0)
    operating_profit = ebit - interest_expense + interest_income
    pbt = operating_profit + interest_income - interest_expense  # simple: OP already after most items
    # Align: profit_before_tax ≈ operating_profit - finance? Use PBT = EBIT - interest_exp + interest_inc
    pbt = ebit - interest_expense + interest_income
    tax = max(pbt, 0.0) * tax_rate
    ni = pbt - tax
    ni_parent = ni - minority_ni
    ni_ex = ni_parent - nonrecurring
    selling = selling if selling is not None else revenue * 0.08
    admin = admin if admin is not None else revenue * 0.06
    return {
        "revenue": revenue,
        "cost_of_revenue": cogs,
        "selling_expense": selling,
        "admin_expense": admin,
        "rd_expense": rd,
        "finance_expense": interest_expense - interest_income,
        "interest_expense_gross": interest_expense,
        "interest_income": interest_income,
        "operating_profit": operating_profit,
        "non_operating_income_net": 0.0,
        "profit_before_tax": pbt,
        "income_tax_expense": tax,
        "net_income": ni,
        "net_income_parent": ni_parent,
        "net_income_ex_nonrecurring_parent": ni_ex,
        "nonrecurring_profit_parent": nonrecurring,
        "depreciation_amortization": da,
        "investment_income": 0.0,
        "fair_value_change": 0.0,
        "asset_disposal_gain": 0.0,
    }


def _bs(
    *,
    cash: float,
    restricted: float = 0.0,
    ar: float,
    inventory: float,
    ap: float,
    other_current: float = 0.0,
    ppe_and_other: float,
    goodwill: float = 0.0,
    st_debt: float,
    lt_debt: float,
    equity_parent: float,
    minority: float = 0.0,
    notes_rec: float = 0.0,
    notes_pay: float = 0.0,
    contract_assets: float = 0.0,
    contract_liab: float = 0.0,
    ar_fin: float = 0.0,
) -> dict[str, float]:
    current_assets = cash + restricted + ar + notes_rec + ar_fin + contract_assets + inventory + other_current
    total_assets = current_assets + ppe_and_other + goodwill
    debt = st_debt + lt_debt
    # lease 0
    total_equity = equity_parent + minority
    # liabilities = total_assets - equity (identity)
    other_liab = 0.0
    operating_liab = ap + notes_pay + contract_liab
    total_liab = debt + operating_liab + other_liab
    # Adjust other long-term / balancing item into ppe or other_liab to keep identity
    gap = total_assets - total_liab - total_equity
    if abs(gap) > 0.01:
        # put residual into a balancing liability if positive assets leftover, else shrink ppe
        if gap > 0:
            # more assets than claims: increase other equity? better increase liabilities via 'balancing'
            # We add to accounts... actually add to a residual in total_liabilities by bumping a plug.
            # Add plug to 'other' by increasing AP? No. Increase total_liabilities conceptually via long-term.
            # We'll store the plug by adjusting ppe down/up so identity holds: total_assets' = total_liab + equity
            ppe_and_other = ppe_and_other - gap
            total_assets = current_assets + ppe_and_other + goodwill
        else:
            ppe_and_other = ppe_and_other - gap
            total_assets = current_assets + ppe_and_other + goodwill
    return {
        "cash_and_equivalents": cash,
        "restricted_cash": restricted,
        "trading_financial_assets": 0.0,
        "accounts_receivable": ar,
        "notes_receivable": notes_rec,
        "accounts_receivable_financing": ar_fin,
        "contract_assets": contract_assets,
        "inventory": inventory,
        "accounts_payable": ap,
        "notes_payable": notes_pay,
        "contract_liabilities": contract_liab,
        "total_assets": total_assets,
        "total_liabilities": total_liab,
        "total_equity": total_equity,
        "equity_parent": equity_parent,
        "minority_interest": minority,
        "short_term_borrowings": st_debt,
        "current_portion_long_term_debt": 0.0,
        "long_term_borrowings": lt_debt,
        "bonds_payable": 0.0,
        "lease_liabilities_current": 0.0,
        "lease_liabilities_noncurrent": 0.0,
        "goodwill": goodwill,
        "intangible_assets": 0.0,
        "development_expenditure_capitalized": 0.0,
    }


def _cf(*, cfo: float, capex: float, interest_paid: float, taxes: float, cfi: float | None = None, cff: float | None = None) -> dict[str, float]:
    return {
        "cash_flow_operating": cfo,
        "cash_paid_for_capex_ppe": capex,  # positive amount
        "cash_received_from_asset_disposal": 0.0,
        "cash_paid_for_acquisitions": 0.0,
        "cash_paid_for_interest": interest_paid,
        "cash_paid_for_taxes": taxes,
        "cash_flow_investing": cfi if cfi is not None else -capex,
        "cash_flow_financing": cff if cff is not None else 0.0,
    }


def price_series(
    start: date,
    end: date,
    *,
    start_price: float,
    end_price: float,
    shares: float,
    high_price: float | None = None,
    high_date: date | None = None,
    turnover: float = 50_000_000,
    step: int = 7,
    shares_path: list[tuple[date, float]] | None = None,
) -> list[dict]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    # subsample
    days = days[:: max(1, step // 5)] or days
    if not days:
        return []
    if days[-1] != end and end.weekday() < 5:
        days.append(end)
    elif days[-1] != end:
        # last Friday on or before end
        last = end
        while last.weekday() >= 5:
            last -= timedelta(days=1)
        if last not in days:
            days.append(last)
    bars = []
    high_price = high_price or max(start_price, end_price)
    high_date = high_date or start

    def px(day: date) -> float:
        # piecewise linear start -> high_date -> end
        if day <= high_date:
            span = max((high_date - start).days, 1)
            t = (day - start).days / span
            return start_price + (high_price - start_price) * t
        span = max((end - high_date).days, 1)
        t = (day - high_date).days / span
        return high_price + (end_price - high_price) * t

    def sh(day: date) -> float:
        if not shares_path:
            return shares
        cur = shares_path[0][1]
        for dt, s in shares_path:
            if dt <= day:
                cur = s
            else:
                break
        return cur

    # total return index starts at 1000, tracks close (no dividends in fixtures unless price path implies)
    base = None
    for day in days:
        p = px(day)
        s = sh(day)
        if base is None:
            base = p
        tri = 1000.0 * p / base
        bars.append(
            {
                "price_date": day.isoformat(),
                "close_unadjusted": p,
                "close_adjusted": p,
                "adjustment_method": "forward",
                "total_return_index": tri,
                "daily_turnover_value": turnover,
                "total_shares": s,
                "diluted_shares": s,
                "market_cap": p * s,
                "suspension_flag": False,
            }
        )
    return bars


def default_impairment(all_no: bool = True, yes_ids: list[str] | None = None) -> list[dict]:
    questions = [
        ("IMP-01", "核心产品或服务是否被技术替代？"),
        ("IMP-02", "核心客户是否永久流失或供应商地位下降？"),
        ("IMP-03", "市场份额、渠道或成本优势是否发生结构性恶化？"),
        ("IMP-04", "行业长期需求是否出现不可逆收缩？"),
        ("IMP-05", "ROIC是否连续多年低于资本成本且看不到修复路径？"),
        ("IMP-06", "资产负债表是否需要持续外部融资才能维持经营？"),
        ("IMP-07", "是否存在严重治理、关联交易或资本分配问题？"),
        ("IMP-08", "核心资产是否存在重大减值、抵押、冻结或产权争议？"),
    ]
    yes_ids = set(yes_ids or [])
    out = []
    for iid, q in questions:
        ans = "YES" if iid in yes_ids else "NO"
        out.append(
            {
                "item_id": iid,
                "question": q,
                "answer": ans,
                "evidence_summary": "合成夹具中的结构化排查结论" if all_no or iid in yes_ids else None,
                "evidence_refs": ["EV-001"] if ans != "UNKNOWN" else [],
                "material_to_long_term_earnings": True if ans == "YES" else False,
            }
        )
    return out


def _good_quality_years(scale: float = 1.0, roic_path: list[float] | None = None) -> dict[int, dict]:
    """Stable high-quality industrial profile, ROIC around 20%."""
    data = {}
    rev0 = 1_000_000_000 * scale
    all_years = [2020, *YEARS]
    for i, year in enumerate(all_years):
        g = 1.05 ** (year - 2021)
        revenue = rev0 * g
        ebit = revenue * 0.20
        interest = 7_500_000 * scale
        da = revenue * 0.04
        inc = _income_from_operating(
            revenue=revenue,
            gross_margin=0.40,
            ebit=ebit,
            interest_expense=interest,
            tax_rate=0.25,
            da=da,
        )
        # Target IC so ROIC ≈ path or 20%
        nopat = ebit * 0.75
        target_roic = (roic_path[i] if roic_path else 0.20)
        ic = nopat / target_roic
        equity = 800_000_000 * scale * g
        debt_st = 40_000_000 * scale
        debt_lt = 110_000_000 * scale
        cash = equity + debt_st + debt_lt - ic
        # Keep cash reasonable
        if cash < 50_000_000 * scale:
            cash = 80_000_000 * scale
            equity = ic - (debt_st + debt_lt) + cash
        ar = revenue * 0.12
        inv = inc["cost_of_revenue"] * 0.15
        ap = inc["cost_of_revenue"] * 0.10
        bs = _bs(
            cash=cash,
            ar=ar,
            inventory=inv,
            ap=ap,
            ppe_and_other=ic * 0.7,
            st_debt=debt_st,
            lt_debt=debt_lt,
            equity_parent=equity * 0.975,
            minority=equity * 0.025,
            goodwill=0.0,
        )
        cfo = inc["net_income"] * 1.12
        capex = revenue * 0.04
        flow = {**inc, **_cf(cfo=cfo, capex=capex, interest_paid=interest, taxes=inc["income_tax_expense"])}
        data[year] = {"flow": flow, "stock_end": bs}
    return data


def company_good_cheap() -> dict:
    sid, symbol, exch = "GOOD_CHEAP", "600001.SH", "SH"
    years = _good_quality_years()
    stmts = statements_from_years(sid, symbol, exch, years)
    prices = price_series(
        date(2019, 1, 2),
        AS_OF,
        start_price=1.15,
        high_price=1.30,
        high_date=date(2025, 10, 15),
        end_price=0.70,
        shares=1_000_000_000,
        turnover=80_000_000,
    )
    evidence = [
        {
            "evidence_id": "EV-GC-001",
            "security_id": sid,
            "category": "TEMPORARY_SHOCK",
            "claim": "近期股价下跌主要来自短期行业去库存，核心客户订单并未结构性消失",
            "evidence_summary": "公司2026年半年报与行业协会开工率数据显示需求为阶段性收缩，毛利率仍稳定",
            "source_name": "公司正式公告 / 行业协会原始统计",
            "source_url_or_doc_id": "fixture://GOOD_CHEAP/announcement-2026H1",
            "published_at": "2026-08-26",
            "evidence_grade": "A",
            "supports_reversibility": True,
            "review_status": "CONFIRMED",
            "reviewer": "fixture",
            "reviewed_at": "2026-09-01",
        }
    ]
    return {
        "security_id": sid,
        "symbol": symbol,
        "company_name": "优质低估合成公司",
        "exchange": exch,
        "statements": stmts,
        "market": {
            "prices": prices,
            "listing_date": "2010-01-15",
            "risk_warning_flag": False,
            "delisting_risk_flag": False,
            "suspension_flag": False,
            "industry": "machinery",
            "sector": "industrials",
            "is_financial": False,
            "is_cyclical": False,
            "levered_beta": 1.0,
            "reasonable_ev_ebit": 12.0,
        },
        "audit_opinions": [
            {"report_period": f"{y}-12-31", "available_date": f"{y+1}-04-01", "opinion": "UNQUALIFIED"}
            for y in YEARS
        ],
        "governance_events": [],
        "evidence": evidence,
        "impairment_checks": default_impairment(True),
        "historical_versions_available": True,
    }


def company_good_expensive() -> dict:
    base = company_good_cheap()
    sid, symbol = "GOOD_EXPENSIVE", "600002.SH"
    # Rebuild statements with new ids
    years = _good_quality_years()
    stmts = statements_from_years(sid, symbol, "SH", years)
    prices = price_series(
        date(2019, 1, 2),
        AS_OF,
        start_price=4.8,
        high_price=5.5,
        high_date=date(2026, 6, 1),
        end_price=5.2,
        shares=1_000_000_000,
        turnover=90_000_000,
    )
    base.update(
        {
            "security_id": sid,
            "symbol": symbol,
            "company_name": "优质高估合成公司",
            "statements": stmts,
            "evidence": [],  # no mispricing evidence
            "impairment_checks": default_impairment(True),
        }
    )
    base["market"] = dict(base["market"])
    base["market"]["prices"] = prices
    return base


def company_cheap_trap() -> dict:
    sid, symbol, exch = "CHEAP_TRAP", "000001.SZ", "SZ"
    data = {}
    rev0 = 1_000_000_000
    roics = [0.18, 0.16, 0.14, 0.10, 0.07]  # continuous decline, last 3 drop 7pp
    for i, year in enumerate(YEARS):
        revenue = rev0 * (1.08 ** i)
        roic = roics[i]
        ebit_margin = 0.16 - 0.02 * i  # shrinking
        ebit = revenue * ebit_margin
        interest = 25_000_000
        da = revenue * 0.04
        inc = _income_from_operating(revenue=revenue, gross_margin=0.32 - 0.02 * i, ebit=ebit, interest_expense=interest, da=da, tax_rate=0.25)
        nopat = ebit * 0.75
        ic = nopat / roic if roic else nopat / 0.05
        equity = ic * 0.55
        debt_st = ic * 0.25
        debt_lt = ic * 0.30
        cash = equity + debt_st + debt_lt - ic
        # Receivables and inventory grow much faster than revenue in 2024-2025
        ar_ratio = 0.12 + (0.25 if year >= 2024 else (0.18 if year >= 2023 else 0.0))
        inv_ratio = 0.15 + (0.25 if year >= 2024 else (0.18 if year >= 2023 else 0.0))
        ar = revenue * ar_ratio
        inv = inc["cost_of_revenue"] * inv_ratio
        ap = inc["cost_of_revenue"] * 0.08
        bs = _bs(cash=max(cash, 20_000_000), ar=ar, inventory=inv, ap=ap, ppe_and_other=ic * 0.8, st_debt=debt_st, lt_debt=debt_lt, equity_parent=equity, goodwill=0.0)
        # Poor CFO conversion < 0.5
        cfo = inc["net_income"] * 0.35
        capex = revenue * 0.05
        flow = {**inc, **_cf(cfo=cfo, capex=capex, interest_paid=interest, taxes=inc["income_tax_expense"])}
        data[year] = {"flow": flow, "stock_end": bs}
    stmts = statements_from_years(sid, symbol, exch, data)
    prices = price_series(date(2019, 1, 2), AS_OF, start_price=2.0, high_price=2.2, high_date=date(2023, 1, 15), end_price=0.40, shares=800_000_000, turnover=30_000_000)
    return {
        "security_id": sid,
        "symbol": symbol,
        "company_name": "低PE陷阱合成公司",
        "exchange": exch,
        "statements": stmts,
        "market": {
            "prices": prices,
            "listing_date": "2008-06-01",
            "industry": "trade",
            "sector": "consumer",
            "is_financial": False,
            "is_cyclical": False,
            "levered_beta": 1.1,
            "reasonable_ev_ebit": 10.0,
        },
        "audit_opinions": [{"report_period": f"{y}-12-31", "available_date": f"{y+1}-04-01", "opinion": "UNQUALIFIED"} for y in YEARS],
        "governance_events": [],
        "evidence": [],
        "impairment_checks": default_impairment(True),
        "historical_versions_available": True,
    }


def company_cyclical_trough() -> dict:
    sid, symbol, exch = "CYCLICAL_TROUGH", "000002.SZ", "SZ"
    # Mid-cycle healthy; last year at trough but still slightly positive (avoid auto R-012 if possible)
    # EBIT margins: 0.18, 0.17, 0.12, 0.06, 0.03
    margins = [0.18, 0.17, 0.12, 0.06, 0.03]
    data = {}
    rev0 = 2_000_000_000
    for i, year in enumerate(YEARS):
        revenue = rev0 * (1.02 ** i)
        ebit = revenue * margins[i]
        interest = 20_000_000
        da = revenue * 0.05
        inc = _income_from_operating(revenue=revenue, gross_margin=0.22 + margins[i] * 0.4, ebit=max(ebit, revenue * 0.02), interest_expense=interest, da=da)
        # Keep TTM 2025/2026 still positive
        nopat = inc["operating_profit"]  # not used
        ic = 2_000_000_000
        equity = 1_500_000_000
        debt_st, debt_lt, cash = 200_000_000, 400_000_000, 100_000_000
        ar = revenue * 0.10
        inv = inc["cost_of_revenue"] * 0.18
        ap = inc["cost_of_revenue"] * 0.12
        bs = _bs(cash=cash, ar=ar, inventory=inv, ap=ap, ppe_and_other=2_200_000_000, st_debt=debt_st, lt_debt=debt_lt, equity_parent=equity)
        cfo = max(inc["net_income"], inc["net_income"] * 0.9)
        capex = revenue * 0.05
        flow = {**inc, **_cf(cfo=cfo, capex=capex, interest_paid=interest, taxes=inc["income_tax_expense"])}
        data[year] = {"flow": flow, "stock_end": bs}
    # 2026 still trough-ish but positive
    last = data[2025]
    ytd = {"flow": {k: (v * 0.9 if k in FLOW_FIELDS else v) for k, v in last["flow"].items()}, "stock_end": last["stock_end"]}
    stmts = statements_from_years(sid, symbol, exch, data, ytd_2026=ytd)
    # Current PE looks expensive because TTM earnings are at trough; price not collapsed enough to look cheap on current PE
    prices = price_series(date(2019, 1, 2), AS_OF, start_price=3.0, high_price=3.2, high_date=date(2022, 6, 1), end_price=2.4, shares=1_200_000_000, turnover=40_000_000)
    return {
        "security_id": sid,
        "symbol": symbol,
        "company_name": "周期底部合成公司",
        "exchange": exch,
        "statements": stmts,
        "market": {
            "prices": prices,
            "listing_date": "2005-03-01",
            "industry": "steel",
            "sector": "steel",
            "is_financial": False,
            "is_cyclical": True,
            "levered_beta": 1.3,
            "reasonable_ev_ebit": 8.0,
        },
        "audit_opinions": [{"report_period": f"{y}-12-31", "available_date": f"{y+1}-04-01", "opinion": "UNQUALIFIED"} for y in YEARS],
        "evidence": [
            {
                "evidence_id": "EV-CT-001",
                "security_id": sid,
                "category": "CYCLICAL_DOWNTURN",
                "claim": "当前盈利处于周期低谷，中周期盈利能力仍在",
                "evidence_summary": "行业协会产能利用率和历史利润率显示本轮位于低分位，并非需求永久消失",
                "source_name": "行业协会原始统计",
                "source_url_or_doc_id": "fixture://CYCLICAL_TROUGH/industry-2026",
                "published_at": "2026-06-01",
                "evidence_grade": "A",
                "supports_reversibility": True,
                "review_status": "CONFIRMED",
                "reviewer": "fixture",
                "reviewed_at": "2026-06-15",
            }
        ],
        "impairment_checks": default_impairment(True),
        "historical_versions_available": True,
    }


def company_structural_decline() -> dict:
    sid, symbol, exch = "STRUCTURAL_DECLINE", "000003.SZ", "SZ"
    data = {}
    rev0 = 1_200_000_000
    for i, year in enumerate(YEARS):
        revenue = rev0 * (0.88 ** i)  # ~12% annual decline
        gm = 0.30 - 0.04 * i
        em = 0.12 - 0.03 * i
        ebit = revenue * max(em, 0.01)
        interest = 15_000_000
        da = revenue * 0.04
        inc = _income_from_operating(revenue=revenue, gross_margin=max(gm, 0.08), ebit=ebit, interest_expense=interest, da=da)
        equity = 700_000_000 * (0.92 ** i)
        ic = 900_000_000
        bs = _bs(
            cash=80_000_000,
            ar=revenue * 0.15,
            inventory=inc["cost_of_revenue"] * 0.20,
            ap=inc["cost_of_revenue"] * 0.08,
            ppe_and_other=800_000_000,
            st_debt=100_000_000,
            lt_debt=200_000_000,
            equity_parent=equity,
        )
        cfo = inc["net_income"] * 0.7
        capex = revenue * 0.04
        flow = {**inc, **_cf(cfo=cfo, capex=capex, interest_paid=interest, taxes=inc["income_tax_expense"])}
        data[year] = {"flow": flow, "stock_end": bs}
    stmts = statements_from_years(sid, symbol, exch, data)
    prices = price_series(date(2019, 1, 2), AS_OF, start_price=3.0, high_price=3.2, high_date=date(2021, 6, 1), end_price=1.5, shares=900_000_000, turnover=25_000_000)
    return {
        "security_id": sid,
        "symbol": symbol,
        "company_name": "结构恶化合成公司",
        "exchange": exch,
        "statements": stmts,
        "market": {
            "prices": prices,
            "listing_date": "2006-01-01",
            "industry": "legacy_hardware",
            "sector": "tech",
            "is_financial": False,
            "is_cyclical": False,
            "levered_beta": 1.2,
            "reasonable_ev_ebit": 10.0,
        },
        "audit_opinions": [{"report_period": f"{y}-12-31", "available_date": f"{y+1}-04-01", "opinion": "UNQUALIFIED"} for y in YEARS],
        "governance_events": [],
        "evidence": [
            {
                "evidence_id": "EV-SD-001",
                "security_id": sid,
                "category": "STRUCTURAL_DECLINE",
                "claim": "核心产品被替代，份额与利润率持续恶化",
                "evidence_summary": "连续五年收入与份额下降，客户转向替代技术",
                "source_name": "公司正式公告",
                "source_url_or_doc_id": "fixture://STRUCTURAL_DECLINE/annual-2025",
                "published_at": "2026-03-31",
                "evidence_grade": "A",
                "supports_reversibility": False,
                "review_status": "CONFIRMED",
                "reviewer": "fixture",
                "reviewed_at": "2026-04-10",
            }
        ],
        "impairment_checks": default_impairment(False, yes_ids=["IMP-01", "IMP-02", "IMP-03", "IMP-04"]),
        "historical_versions_available": True,
    }


def company_dilution_trap() -> dict:
    sid, symbol, exch = "DILUTION_TRAP", "600003.SH", "SH"
    data = {}
    # NI grows ~50% over 3 years but shares grow 40%, EPS stagnates
    ni_path = [100_000_000, 110_000_000, 120_000_000, 140_000_000, 160_000_000]
    share_path = [
        (date(2019, 1, 1), 1_000_000_000),
        (date(2024, 6, 1), 1_250_000_000),
        (date(2025, 6, 1), 1_500_000_000),
    ]
    for i, year in enumerate(YEARS):
        ni = ni_path[i]
        # back into revenue / ebit
        pbt = ni / 0.75
        ebit = pbt + 8_000_000
        revenue = ebit / 0.12
        inc = _income_from_operating(revenue=revenue, gross_margin=0.28, ebit=ebit, interest_expense=8_000_000, da=revenue * 0.04)
        # overwrite NI to exact path
        inc["net_income"] = ni
        inc["net_income_parent"] = ni
        inc["net_income_ex_nonrecurring_parent"] = ni
        inc["profit_before_tax"] = pbt
        inc["income_tax_expense"] = pbt - ni
        equity = 1_000_000_000 + i * 200_000_000  # equity rises with issuance
        bs = _bs(cash=150_000_000, ar=revenue * 0.12, inventory=inc["cost_of_revenue"] * 0.12, ap=inc["cost_of_revenue"] * 0.10, ppe_and_other=1_200_000_000, st_debt=50_000_000, lt_debt=100_000_000, equity_parent=equity)
        cfo = ni * 1.0
        capex = revenue * 0.04
        flow = {**inc, **_cf(cfo=cfo, capex=capex, interest_paid=8_000_000, taxes=inc["income_tax_expense"])}
        data[year] = {"flow": flow, "stock_end": bs}
    stmts = statements_from_years(sid, symbol, exch, data)
    prices = price_series(
        date(2019, 1, 2),
        AS_OF,
        start_price=2.0,
        end_price=1.6,
        high_price=2.1,
        high_date=date(2022, 1, 10),
        shares=1_500_000_000,
        shares_path=share_path,
        turnover=40_000_000,
    )
    return {
        "security_id": sid,
        "symbol": symbol,
        "company_name": "股权稀释陷阱合成公司",
        "exchange": exch,
        "statements": stmts,
        "market": {
            "prices": prices,
            "share_events": [
                {"event_date": "2024-06-01", "available_date": "2024-06-01", "event_type": "issuance", "shares_after": 1_250_000_000},
                {"event_date": "2025-06-01", "available_date": "2025-06-01", "event_type": "issuance", "shares_after": 1_500_000_000},
            ],
            "listing_date": "2012-05-01",
            "industry": "manufacturing",
            "sector": "industrials",
            "is_financial": False,
            "levered_beta": 1.0,
            "reasonable_ev_ebit": 12.0,
        },
        "audit_opinions": [{"report_period": f"{y}-12-31", "available_date": f"{y+1}-04-01", "opinion": "UNQUALIFIED"} for y in YEARS],
        "evidence": [],
        "impairment_checks": default_impairment(True),
        "historical_versions_available": True,
    }


def company_restated_data() -> dict:
    sid, symbol, exch = "RESTATED_DATA", "600004.SH", "SH"
    # Start from good-quality template, then change 2022 NI in v2
    years = _good_quality_years(scale=0.8)
    stmts_v1 = statements_from_years(sid, symbol, exch, years, version_id="v1", version_chain="v1", is_restatement=False)
    # Restate 2022 only: cut NI-related flow items in half; new version available 2024-03-31 after close
    years_v2 = deepcopy(years)
    flow = years_v2[2022]["flow"]
    for k in (
        "operating_profit", "profit_before_tax", "income_tax_expense", "net_income",
        "net_income_parent", "net_income_ex_nonrecurring_parent", "cash_flow_operating",
    ):
        if k in flow:
            flow[k] = flow[k] * 0.5
    override = {}
    restated_ann = _ann(date(2024, 3, 31), 18)
    for stype in ("Q1", "H1", "Q3", "ANNUAL"):
        override[(2022, stype, "v2")] = restated_ann
    # Only emit restated 2022 statements as v2 (prior years remain v1-only)
    stmts_v2 = statements_from_years(
        sid, symbol, exch, {2022: years_v2[2022]},
        version_id="v2",
        version_chain="v2",
        is_restatement=True,
        include_2026_ytd=False,
        announce_override=override,
    )
    stmts = stmts_v1 + stmts_v2
    prices = price_series(date(2019, 1, 2), AS_OF, start_price=1.0, end_price=0.95, shares=800_000_000, turnover=20_000_000)
    return {
        "security_id": sid,
        "symbol": symbol,
        "company_name": "财报重述合成公司",
        "exchange": exch,
        "statements": stmts,
        "market": {
            "prices": prices,
            "listing_date": "2011-01-01",
            "industry": "manufacturing",
            "sector": "industrials",
            "is_financial": False,
            "levered_beta": 1.0,
            "reasonable_ev_ebit": 12.0,
        },
        "audit_opinions": [{"report_period": f"{y}-12-31", "available_date": f"{y+1}-04-01", "opinion": "UNQUALIFIED"} for y in YEARS],
        "evidence": [],
        "impairment_checks": default_impairment(True),
        "historical_versions_available": True,
    }


def company_missing_data() -> dict:
    sid, symbol, exch = "MISSING_DATA", "600005.SH", "SH"
    # Only one incomplete year, missing CFO / capex / several BS items
    period = date(2025, 12, 31)
    stmt = {
        "security_id": sid,
        "symbol": symbol,
        "exchange": exch,
        "report_period": period.isoformat(),
        "announcement_datetime": _ann(date(2026, 3, 31)),
        "statement_type": "ANNUAL",
        "version_id": "v1",
        "version_chain": "v1",
        "is_restatement": False,
        "source": "synthetic_fixture",
        "currency": "CNY",
        "income": {"revenue": _fv(None), "net_income_parent": _fv(50_000_000)},
        "balance": {"total_assets": _fv(800_000_000)},
        "cashflow": {},
    }
    prices = price_series(date(2024, 1, 2), AS_OF, start_price=1.0, end_price=1.0, shares=500_000_000, turnover=15_000_000)
    return {
        "security_id": sid,
        "symbol": symbol,
        "company_name": "关键数据缺失合成公司",
        "exchange": exch,
        "statements": [stmt],
        "market": {
            "prices": prices,
            "listing_date": "2015-01-01",
            "industry": "manufacturing",
            "sector": "industrials",
            "is_financial": False,
            "levered_beta": 1.0,
        },
        "audit_opinions": [],
        "evidence": [],
        "impairment_checks": [],
        "historical_versions_available": True,
    }


def company_negative_fcf_growth() -> dict:
    sid, symbol, exch = "NEGATIVE_FCF_GROWTH", "600006.SH", "SH"
    data = {}
    rev0 = 800_000_000
    for i, year in enumerate(YEARS):
        revenue = rev0 * (1.18 ** i)  # high growth
        ebit = revenue * 0.16
        interest = 5_000_000
        da = revenue * 0.03
        inc = _income_from_operating(revenue=revenue, gross_margin=0.38, ebit=ebit, interest_expense=interest, da=da)
        nopat = ebit * 0.75
        ic = nopat / 0.16
        equity = ic * 0.8
        debt_st, debt_lt = ic * 0.1, ic * 0.2
        cash = equity + debt_st + debt_lt - ic
        bs = _bs(cash=max(cash, 40_000_000), ar=revenue * 0.11, inventory=inc["cost_of_revenue"] * 0.12, ap=inc["cost_of_revenue"] * 0.11, ppe_and_other=ic * 0.9, st_debt=debt_st, lt_debt=debt_lt, equity_parent=equity)
        # Strong CFO but heavy growth capex — especially last 2 years and TTM
        cfo = inc["net_income"] * 1.05
        capex_ratio = 0.08 if year < 2024 else 0.22
        capex = revenue * capex_ratio
        flow = {**inc, **_cf(cfo=cfo, capex=capex, interest_paid=interest, taxes=inc["income_tax_expense"])}
        data[year] = {"flow": flow, "stock_end": bs}
    # 2026 continues heavy capex
    last = data[2025]
    ytd_flow = {k: v * 1.15 for k, v in last["flow"].items()}
    ytd_flow["cash_paid_for_capex_ppe"] = last["flow"]["revenue"] * 1.15 * 0.24
    ytd_flow["cash_flow_operating"] = last["flow"]["net_income"] * 1.15 * 1.05
    stmts = statements_from_years(sid, symbol, exch, data, ytd_2026={"flow": ytd_flow, "stock_end": {k: v * 1.1 for k, v in last["stock_end"].items()}})
    prices = price_series(date(2019, 1, 2), AS_OF, start_price=2.0, end_price=2.4, high_price=2.5, high_date=date(2026, 1, 15), shares=700_000_000, turnover=35_000_000)
    return {
        "security_id": sid,
        "symbol": symbol,
        "company_name": "成长性资本开支短期负FCF合成公司",
        "exchange": exch,
        "statements": stmts,
        "market": {
            "prices": prices,
            "listing_date": "2014-08-01",
            "industry": "growth_manufacturing",
            "sector": "industrials",
            "is_financial": False,
            "is_cyclical": False,
            "levered_beta": 1.1,
            "reasonable_ev_ebit": 15.0,
        },
        "audit_opinions": [{"report_period": f"{y}-12-31", "available_date": f"{y+1}-04-01", "opinion": "UNQUALIFIED"} for y in YEARS],
        "evidence": [],
        "impairment_checks": default_impairment(True),
        "historical_versions_available": True,
    }


def risk_free_curve() -> list[dict]:
    pts = []
    d = date(2018, 1, 15)
    while d <= AS_OF:
        pts.append({"rate_date": d.isoformat(), "yield_10y": 0.025, "source": "cn_gov_10y_yield_pit_fixture"})
        # monthly
        month = d.month + 1
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        d = date(year, month, 15)
    return pts


def build_universe() -> dict:
    companies = [
        company_good_cheap(),
        company_good_expensive(),
        company_cheap_trap(),
        company_cyclical_trough(),
        company_structural_decline(),
        company_dilution_trap(),
        company_restated_data(),
        company_missing_data(),
        company_negative_fcf_growth(),
    ]
    return {
        "as_of_date": AS_OF.isoformat(),
        "data_source": "synthetic_fixtures_v0.1",
        "data_version": "0.1.0",
        "final_vintage_only": False,
        "historical_versions_available": True,
        "risk_free_curve": risk_free_curve(),
        "companies": companies,
    }


def write_universe(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "universe.json"
    payload = build_universe()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Also write individual company files for the directory loader path
    cdir = dest / "companies"
    cdir.mkdir(exist_ok=True)
    for c in payload["companies"]:
        (cdir / f"{c['security_id']}.json").write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = {k: v for k, v in payload.items() if k != "companies"}
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    target = Path(__file__).parent / "universe"
    path = write_universe(target)
    print(f"wrote {path}")
