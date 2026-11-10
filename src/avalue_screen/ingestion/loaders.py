"""Load local JSON / CSV fixtures. No live market API is required."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import StatementType
from avalue_screen.domain.errors import DataContractError
from avalue_screen.domain.models import (
    AuditRecord,
    CompanyInput,
    EvidenceRecord,
    FieldValue,
    FinancialStatement,
    GovernanceEvent,
    ImpairmentCheck,
    IndexBar,
    MarketSnapshot,
    PriceBar,
    RiskFreeRatePoint,
    ShareEvent,
    UniversePayload,
)
from avalue_screen.ingestion.field_mapper import FieldMapper, _coerce_field
from avalue_screen.ingestion.unit_normalizer import normalize_field_value
from avalue_screen.utils import parse_date, parse_datetime


def load_input(
    path: str | Path,
    config: ScreenConfig,
    mapper: FieldMapper | None = None,
) -> UniversePayload:
    path = Path(path)
    if not path.exists():
        raise DataContractError(f"Input path not found: {path}")
    if path.is_file():
        if path.suffix.lower() == ".json":
            return _normalize_payload(_load_json(path), config, mapper, source_name=str(path))
        if path.suffix.lower() in {".csv", ".tsv"}:
            return _normalize_payload(_load_csv_bundle(path.parent, config), config, mapper)
        raise DataContractError(f"Unsupported input file type: {path}")
    # Directory: prefer universe.json, else companies/*.json, else CSV bundle
    universe_json = path / "universe.json"
    if universe_json.exists():
        return _normalize_payload(_load_json(universe_json), config, mapper, source_name=str(universe_json))
    company_files = sorted(path.glob("*.json")) + sorted((path / "companies").glob("*.json") if (path / "companies").exists() else [])
    # Deduplicate
    seen: set[Path] = set()
    unique_files: list[Path] = []
    for f in company_files:
        rp = f.resolve()
        if rp in seen or f.name in {"universe.json", "manifest.json"}:
            continue
        seen.add(rp)
        unique_files.append(f)
    if unique_files:
        companies = []
        extra: dict[str, Any] = {}
        for f in unique_files:
            raw = _load_json(f)
            if "companies" in raw:
                payload = raw
            elif "security_id" in raw or "symbol" in raw:
                payload = {"companies": [raw]}
            else:
                extra.update(raw)
                continue
            part = _normalize_payload(payload, config, mapper, source_name=str(f))
            companies.extend(part.companies)
            if part.risk_free_curve and not extra.get("risk_free_curve"):
                extra["risk_free_curve"] = [p.model_dump(mode="json") for p in part.risk_free_curve]
            if part.trading_days and not extra.get("trading_days"):
                extra["trading_days"] = [d.isoformat() for d in part.trading_days]
        meta = extra
        # Also merge sidecar files
        sidecar = _load_sidecars(path)
        meta.update({k: v for k, v in sidecar.items() if k not in meta or not meta[k]})
        payload = {"companies": [c.model_dump(mode="json") for c in companies], **meta}
        return _normalize_payload(payload, config, mapper, source_name=str(path))
    return _normalize_payload(_load_csv_bundle(path, config), config, mapper)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise DataContractError(f"JSON root must be an object: {path}")
    return data


def _load_sidecars(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("risk_free_curve.json", "index_prices.json", "calendar.json", "meta.json"):
        f = path / name
        if f.exists():
            out.update(_load_json(f))
    # CSV sidecars
    rfc = path / "risk_free_curve.csv"
    if rfc.exists():
        df = pd.read_csv(rfc)
        out["risk_free_curve"] = df.to_dict(orient="records")
    cal = path / "trading_days.csv"
    if cal.exists():
        df = pd.read_csv(cal)
        col = "trading_day" if "trading_day" in df.columns else df.columns[0]
        out["trading_days"] = [str(x) for x in df[col].tolist()]
    return out


def _load_csv_bundle(path: Path, config: ScreenConfig) -> dict[str, Any]:
    """Load a directory of CSVs: securities.csv, statements.csv, prices.csv, optional others."""
    securities = path / "securities.csv"
    statements = path / "statements.csv"
    if not securities.exists() or not statements.exists():
        raise DataContractError(
            f"CSV input requires securities.csv and statements.csv under {path}"
        )
    secs = pd.read_csv(securities)
    stmts = pd.read_csv(statements)
    prices_path = path / "prices.csv"
    prices = pd.read_csv(prices_path) if prices_path.exists() else pd.DataFrame()

    companies: list[dict[str, Any]] = []
    for _, row in secs.iterrows():
        sid = str(row.get("security_id") or row.get("symbol"))
        symbol = str(row.get("symbol") or sid)
        company_stmts = stmts[stmts["security_id"].astype(str) == sid] if "security_id" in stmts.columns else stmts[stmts["symbol"].astype(str) == symbol]
        grouped: dict[tuple, dict[str, Any]] = {}
        for _, srow in company_stmts.iterrows():
            key = (
                str(srow.get("report_period")),
                str(srow.get("version_id") or "v1"),
                str(srow.get("statement_type") or "ANNUAL"),
            )
            rec = grouped.setdefault(
                key,
                {
                    "security_id": sid,
                    "symbol": symbol,
                    "exchange": srow.get("exchange") or row.get("exchange"),
                    "report_period": srow.get("report_period"),
                    "announcement_datetime": srow.get("announcement_datetime"),
                    "available_date": srow.get("available_date"),
                    "statement_type": srow.get("statement_type") or "ANNUAL",
                    "version_id": srow.get("version_id") or "v1",
                    "version_chain": srow.get("version_chain"),
                    "is_restatement": bool(srow.get("is_restatement", False)),
                    "source": srow.get("source"),
                    "income": {},
                    "balance": {},
                    "cashflow": {},
                },
            )
            field = str(srow.get("field") or srow.get("source_field"))
            block = str(srow.get("statement_block") or srow.get("block") or "income")
            if block not in rec:
                block = "income"
            rec[block][field] = {
                "value": None if pd.isna(srow.get("value")) else float(srow.get("value")),
                "unit": srow.get("unit") or "yuan",
                "currency": srow.get("currency") or "CNY",
                "source": srow.get("source"),
                "source_field": srow.get("source_field") or field,
            }
        market: dict[str, Any] = {
            "listing_date": _maybe(row, "listing_date"),
            "delisting_date": _maybe(row, "delisting_date"),
            "risk_warning_flag": bool(row.get("risk_warning_flag", False)),
            "delisting_risk_flag": bool(row.get("delisting_risk_flag", False)),
            "suspension_flag": bool(row.get("suspension_flag", False)),
            "industry": _maybe(row, "industry"),
            "sector": _maybe(row, "sector"),
            "is_financial": bool(row.get("is_financial", False)),
            "is_cyclical": None if pd.isna(row.get("is_cyclical")) else bool(row.get("is_cyclical")) if "is_cyclical" in row else None,
            "levered_beta": None if "levered_beta" not in row or pd.isna(row.get("levered_beta")) else float(row.get("levered_beta")),
            "prices": [],
        }
        if not prices.empty:
            key_col = "security_id" if "security_id" in prices.columns else "symbol"
            key_val = sid if key_col == "security_id" else symbol
            cprices = prices[prices[key_col].astype(str) == str(key_val)]
            market["prices"] = cprices.to_dict(orient="records")
        companies.append(
            {
                "security_id": sid,
                "symbol": symbol,
                "company_name": _maybe(row, "company_name"),
                "exchange": _maybe(row, "exchange"),
                "statements": list(grouped.values()),
                "market": market,
            }
        )
    sidecar = _load_sidecars(path)
    return {"companies": companies, **sidecar}


def _maybe(row: Any, key: str) -> Any:
    if key not in row:
        return None
    val = row[key]
    if pd.isna(val):
        return None
    return val


def _normalize_payload(
    raw: dict[str, Any],
    config: ScreenConfig,
    mapper: FieldMapper | None,
    source_name: str | None = None,
) -> UniversePayload:
    mapper = mapper or FieldMapper()
    companies_raw = raw.get("companies")
    if companies_raw is None and ("security_id" in raw or "symbol" in raw):
        companies_raw = [raw]
        raw = {k: v for k, v in raw.items() if k not in {"security_id", "symbol", "statements", "market", "company_name"}}
        raw["companies"] = companies_raw
    if not companies_raw:
        raise DataContractError("Input contains no companies")

    companies: list[CompanyInput] = []
    for item in companies_raw:
        companies.append(_normalize_company(item, config, mapper))

    curve = [_parse_rf(x) for x in raw.get("risk_free_curve") or []]
    index_prices = [_parse_index(x) for x in raw.get("index_prices") or []]
    trading_days = [parse_date(x) for x in raw.get("trading_days") or [] if parse_date(x)]
    holidays = [parse_date(x) for x in raw.get("holidays") or [] if parse_date(x)]
    as_of = parse_date(raw.get("as_of_date"))
    return UniversePayload(
        as_of_date=as_of,
        companies=companies,
        risk_free_curve=curve,
        index_prices=index_prices,
        trading_days=trading_days,
        holidays=holidays,
        final_vintage_only=bool(raw.get("final_vintage_only", False)),
        historical_versions_available=raw.get("historical_versions_available"),
        data_source=raw.get("data_source") or source_name,
        data_version=raw.get("data_version"),
    )


def _parse_rf(item: Any) -> RiskFreeRatePoint:
    if isinstance(item, RiskFreeRatePoint):
        return item
    return RiskFreeRatePoint(
        rate_date=parse_date(item.get("rate_date") or item.get("date")),
        yield_10y=float(item.get("yield_10y") if item.get("yield_10y") is not None else item.get("yield")),
        source=item.get("source"),
    )


def _parse_index(item: Any) -> IndexBar:
    if isinstance(item, IndexBar):
        return item
    return IndexBar(
        price_date=parse_date(item.get("price_date") or item.get("date")),
        close=float(item["close"]),
        symbol=item.get("symbol") or "CSI300",
    )


def _normalize_company(item: dict[str, Any] | CompanyInput, config: ScreenConfig, mapper: FieldMapper) -> CompanyInput:
    if isinstance(item, CompanyInput):
        data = item.model_dump()
    else:
        data = dict(item)
    statements = []
    for stmt in data.get("statements") or []:
        statements.append(_normalize_statement(stmt, data, config, mapper))
    data["statements"] = statements
    if "market" in data and data["market"] is not None:
        data["market"] = _normalize_market(data["market"])
    if "audit_opinions" in data:
        data["audit_opinions"] = [
            a if isinstance(a, AuditRecord) else AuditRecord.model_validate(_dates(a, "report_period"))
            for a in data["audit_opinions"]
        ]
    if "governance_events" in data:
        data["governance_events"] = [
            g if isinstance(g, GovernanceEvent) else GovernanceEvent.model_validate(_dates(g, "event_date"))
            for g in data["governance_events"]
        ]
    if "evidence" in data:
        evs = []
        for ev in data["evidence"]:
            if isinstance(ev, EvidenceRecord):
                evs.append(ev)
            else:
                ev = dict(ev)
                ev["published_at"] = parse_date(ev.get("published_at"))
                ev["reviewed_at"] = parse_date(ev.get("reviewed_at"))
                evs.append(EvidenceRecord.model_validate(ev))
        data["evidence"] = evs
    if "impairment_checks" in data:
        data["impairment_checks"] = [
            i if isinstance(i, ImpairmentCheck) else ImpairmentCheck.model_validate(i)
            for i in data["impairment_checks"]
        ]
    return CompanyInput.model_validate(data)


def _dates(obj: dict[str, Any], primary: str) -> dict[str, Any]:
    out = dict(obj)
    if primary in out:
        out[primary] = parse_date(out[primary]) if not isinstance(out[primary], date) else out[primary]
    if out.get("available_date"):
        out["available_date"] = parse_date(out["available_date"])
    if out.get("announcement_datetime"):
        out["announcement_datetime"] = parse_datetime(out["announcement_datetime"])
    return out


def _normalize_statement(
    stmt: dict[str, Any] | FinancialStatement,
    company: dict[str, Any],
    config: ScreenConfig,
    mapper: FieldMapper,
) -> FinancialStatement:
    if isinstance(stmt, FinancialStatement):
        raw = stmt
    else:
        payload = dict(stmt)
        payload.setdefault("security_id", company.get("security_id"))
        payload.setdefault("symbol", company.get("symbol"))
        payload.setdefault("exchange", company.get("exchange"))
        payload["report_period"] = parse_date(payload.get("report_period"))
        payload["available_date"] = parse_date(payload.get("available_date"))
        if payload.get("announcement_datetime"):
            payload["announcement_datetime"] = parse_datetime(payload["announcement_datetime"])
        st = payload.get("statement_type") or "ANNUAL"
        payload["statement_type"] = StatementType(str(st).upper()) if not isinstance(st, StatementType) else st
        for block in ("income", "balance", "cashflow"):
            payload[block] = {
                k: _coerce_field(v) for k, v in (payload.get(block) or {}).items()
            }
        raw = FinancialStatement.model_validate(payload)
    mapped = mapper.map_statement(raw)
    def _norm_block(block: dict[str, FieldValue]) -> dict[str, FieldValue]:
        return {k: normalize_field_value(v, config, field=k) for k, v in block.items()}
    return mapped.model_copy(
        update={
            "income": _norm_block(mapped.income),
            "balance": _norm_block(mapped.balance),
            "cashflow": _norm_block(mapped.cashflow),
            "currency": "CNY",
        }
    )


def _normalize_market(market: dict[str, Any] | MarketSnapshot) -> MarketSnapshot:
    if isinstance(market, MarketSnapshot):
        data = market.model_dump()
    else:
        data = dict(market)
    data["listing_date"] = parse_date(data.get("listing_date"))
    data["delisting_date"] = parse_date(data.get("delisting_date"))
    prices = []
    for p in data.get("prices") or []:
        if isinstance(p, PriceBar):
            prices.append(p)
            continue
        rec = dict(p)
        rec["price_date"] = parse_date(rec.get("price_date") or rec.get("date"))
        prices.append(PriceBar.model_validate(rec))
    data["prices"] = prices
    events = []
    for e in data.get("share_events") or []:
        if isinstance(e, ShareEvent):
            events.append(e)
            continue
        rec = dict(e)
        rec["event_date"] = parse_date(rec.get("event_date") or rec.get("date"))
        rec["available_date"] = parse_date(rec.get("available_date"))
        rec["announcement_datetime"] = parse_datetime(rec.get("announcement_datetime"))
        events.append(ShareEvent.model_validate(rec))
    data["share_events"] = events
    return MarketSnapshot.model_validate(data)
