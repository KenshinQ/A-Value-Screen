"""Universe membership: ordinary A-shares, board filters, ST/financials/suspension."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.models import CompanyInput
from avalue_screen.utils import is_finite, year_fraction


@dataclass
class UniverseDecision:
    included: bool
    reason: str | None
    board: str  # main | chi_next | star | bse | other | non_ordinary
    is_financial: bool
    is_st_or_risk: bool
    is_suspended: bool
    listing_years: float | None
    listing_bucket: str  # FULL | PROVISIONAL | TOO_NEW | UNKNOWN
    liquidity_ok: bool | None
    daily_turnover_cny: float | None
    code_prefix_ok: bool


def _symbol_code(symbol: str) -> str:
    return symbol.split(".")[0].split("-")[0].strip()


def _exchange(company: CompanyInput) -> str:
    if company.exchange:
        return company.exchange.upper()
    suffix = company.symbol.split(".")[-1].upper() if "." in company.symbol else ""
    return {"SH": "SH", "SS": "SH", "SZ": "SZ", "BJ": "BJ", "BSE": "BJ"}.get(suffix, suffix or "")


def _matches_prefix(code: str, prefixes: list[str]) -> bool:
    return any(code.startswith(p) for p in prefixes)


def classify_board(company: CompanyInput, config: ScreenConfig) -> str:
    code = _symbol_code(company.symbol)
    exch = _exchange(company)
    uni = config.section("universe")
    if _matches_prefix(code, list(uni.get("non_ordinary_prefixes", {}).get(exch, []) or [])):
        return "non_ordinary"
    if exch in set(uni.get("bse_exchanges") or []) or _matches_prefix(code, list(uni.get("ordinary_a_share_prefixes", {}).get("BJ", []) or [])):
        # May still be STAR/main if SH/SZ
        pass
    if _matches_prefix(code, list(uni.get("chi_next_prefixes") or [])):
        return "chi_next"
    if _matches_prefix(code, list(uni.get("star_prefixes") or [])):
        return "star"
    main = list((uni.get("main_board_prefixes") or {}).get(exch, []) or [])
    if _matches_prefix(code, main):
        return "main"
    if exch in set(uni.get("bse_exchanges") or []) or exch in {"BJ", "BSE"}:
        return "bse"
    ordinary = list((uni.get("ordinary_a_share_prefixes") or {}).get(exch, []) or [])
    if ordinary and _matches_prefix(code, ordinary):
        return "other"
    if ordinary:
        return "non_ordinary"
    return "other"


def _is_financial(company: CompanyInput, config: ScreenConfig) -> bool:
    if company.market.is_financial:
        return True
    uni = config.section("universe")
    codes = {str(x).lower() for x in (uni.get("financial_sector_codes") or [])}
    keywords = [str(x).lower() for x in (uni.get("financial_industry_keywords") or [])]
    sector = (company.market.sector or "").lower()
    industry = (company.market.industry or "").lower()
    blob = f"{sector} {industry}"
    if sector in codes or industry in codes:
        return True
    return any(k in blob for k in keywords if k)


def classify_security(
    company: CompanyInput,
    as_of: date,
    config: ScreenConfig,
    *,
    turnover: float | None = None,
    suspended_on_as_of: bool | None = None,
) -> UniverseDecision:
    uni = config.section("universe")
    board = classify_board(company, config)
    code = _symbol_code(company.symbol)
    exch = _exchange(company)
    ordinary_prefixes = list((uni.get("ordinary_a_share_prefixes") or {}).get(exch, []) or [])
    code_ok = (not ordinary_prefixes) or _matches_prefix(code, ordinary_prefixes) or board in {"main", "chi_next", "star", "bse"}

    is_fin = _is_financial(company, config)
    is_st = bool(company.market.risk_warning_flag or company.market.delisting_risk_flag)
    name = (company.company_name or "") + " " + company.symbol
    if "ST" in name.upper() or "*ST" in name.upper():
        is_st = True

    # Delisted after as_of must still be in a historical universe; delisted before as_of is out.
    if company.market.delisting_date and company.market.delisting_date <= as_of:
        return UniverseDecision(
            included=False,
            reason="Already delisted as of the screening date",
            board=board,
            is_financial=is_fin,
            is_st_or_risk=True,
            is_suspended=True,
            listing_years=None,
            listing_bucket="UNKNOWN",
            liquidity_ok=None,
            daily_turnover_cny=turnover,
            code_prefix_ok=code_ok,
        )
    # Not yet listed
    if company.market.listing_date and company.market.listing_date > as_of:
        return UniverseDecision(
            included=False,
            reason="Not yet listed as of the screening date",
            board=board,
            is_financial=is_fin,
            is_st_or_risk=is_st,
            is_suspended=False,
            listing_years=None,
            listing_bucket="TOO_NEW",
            liquidity_ok=None,
            daily_turnover_cny=turnover,
            code_prefix_ok=code_ok,
        )

    if uni.get("exclude_non_ordinary", True) and board == "non_ordinary":
        return UniverseDecision(False, "Not an ordinary A-share", board, is_fin, is_st, False, None, "UNKNOWN", None, turnover, False)

    if not uni.get("include_bse", True) and board == "bse":
        return UniverseDecision(False, "BSE excluded by configuration", board, is_fin, is_st, False, None, "UNKNOWN", None, turnover, code_ok)

    if uni.get("main_board_only", False) and board != "main":
        return UniverseDecision(
            False,
            "main_board_only is enabled; ChiNext / STAR / BSE / other boards are dropped",
            board,
            is_fin,
            is_st,
            False,
            None,
            "UNKNOWN",
            None,
            turnover,
            code_ok,
        )

    if uni.get("exclude_financial_sectors", True) and is_fin:
        return UniverseDecision(False, "Financial sector excluded in v0.1", board, True, is_st, False, None, "UNKNOWN", None, turnover, code_ok)

    if uni.get("exclude_st", True) and (company.market.risk_warning_flag or "ST" in (company.company_name or "").upper()):
        # Hard reject is also applied later as G-001; universe can drop them from the working set.
        pass  # still include in the working set so G-001 can be recorded as REJECT rather than silent drop? 
        # SPEC default exclude ST from universe. We'll exclude from included=True but pipeline still scores? 
        # Better: not included in official universe; pipeline can still emit them as REJECT if processed.
        # I'll mark included=False with reason ST so pipeline can optionally record REJECT.
        
    suspended = bool(
        company.market.suspension_flag
        or company.market.listing_suspended_or_terminated
        or suspended_on_as_of
    )
    # Price bars may also mark suspension; caller can pass suspended_on_as_of.

    listing_years = None
    if company.market.listing_date:
        listing_years = year_fraction(
            company.market.listing_date, as_of, float(config.get("screening.listing_year_basis", 365.25))
        )
    full_y = float(config.require("screening.min_listing_years_for_full_grade"))
    obs_y = float(config.require("screening.min_listing_years_for_observation"))
    if listing_years is None:
        bucket = "UNKNOWN"
    elif listing_years < obs_y:
        bucket = "TOO_NEW"
    elif listing_years < full_y:
        bucket = "PROVISIONAL"
    else:
        bucket = "FULL"

    min_to = float(uni.get("min_daily_turnover_cny", 0) or 0)
    liquidity_ok = None
    if is_finite(turnover):
        liquidity_ok = turnover >= min_to
    elif turnover is None:
        liquidity_ok = None  # unknown; do not treat as zero

    included = True
    reason = None
    if uni.get("exclude_non_ordinary", True) and not code_ok and board == "non_ordinary":
        included, reason = False, "Not an ordinary A-share"
    if uni.get("exclude_st", True) and (company.market.risk_warning_flag or "*ST" in name or (company.company_name or "").startswith("ST") or " ST" in f" {name}"):
        included, reason = False, "ST / *ST / risk-warning name excluded from the default universe"
    if uni.get("exclude_delisting_risk", True) and (company.market.delisting_risk_flag or company.market.delisting_in_process):
        included, reason = False, "Delisting-risk security excluded from the default universe"
    if uni.get("exclude_suspended_on_as_of_date", True) and suspended:
        included, reason = False, "Suspended (or no tradeable price) on the as-of date"

    return UniverseDecision(
        included=included,
        reason=reason,
        board=board,
        is_financial=is_fin,
        is_st_or_risk=is_st or company.market.delisting_risk_flag,
        is_suspended=suspended,
        listing_years=listing_years,
        listing_bucket=bucket,
        liquidity_ok=liquidity_ok,
        daily_turnover_cny=turnover,
        code_prefix_ok=code_ok,
    )


def apply_universe_filter(company: CompanyInput, as_of: date, config: ScreenConfig, **kwargs) -> UniverseDecision:
    return classify_security(company, as_of, config, **kwargs)
