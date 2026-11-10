"""Announcement availability and run-mode PIT constraints."""

from __future__ import annotations

from datetime import date, datetime, time

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import FinancialDataMode, PitStatus, RunMode
from avalue_screen.domain.errors import InsufficientPitHistoryError
from avalue_screen.domain.models import CompanyInput, FinancialStatement, UniversePayload
from avalue_screen.utils import (
    TradingCalendar,
    has_time_precision,
    parse_datetime,
    parse_hhmm,
    to_shanghai,
)


def compute_available_date(
    announcement_datetime: datetime | str | None,
    calendar: TradingCalendar,
    config: ScreenConfig,
    *,
    explicit_available_date: date | None = None,
) -> date | None:
    """Determine the first date the information may be used for screening/trading.

    Rules (SPEC 9.1):
    - If a clock time is present: same-session if before market close, else next trading day.
    - If only a calendar date is present: conservatively next trading day.
    - An explicit available_date, when provided, is respected (source of truth from the dataset).
    """
    if explicit_available_date is not None:
        return explicit_available_date
    if announcement_datetime is None:
        return None
    dt = parse_datetime(announcement_datetime)
    if dt is None:
        return None
    close = parse_hhmm(str(config.get("run.market_close_time", "15:00")))
    local = to_shanghai(dt)
    d = local.date()
    date_only_next = bool(config.get("run.date_only_available_next_session", True))
    if not has_time_precision(announcement_datetime) and date_only_next:
        return calendar.next_trading_day(d)
    if local.timetz().replace(tzinfo=None) if False else local.time() >= close:
        return calendar.next_trading_day(d)
    return calendar.trading_day_or_next(d)


def fill_available_dates(
    statements: list[FinancialStatement],
    calendar: TradingCalendar,
    config: ScreenConfig,
) -> list[FinancialStatement]:
    filled: list[FinancialStatement] = []
    for stmt in statements:
        avail = compute_available_date(
            stmt.announcement_datetime,
            calendar,
            config,
            explicit_available_date=stmt.available_date,
        )
        filled.append(stmt.model_copy(update={"available_date": avail}))
    return filled


def statement_is_visible(stmt: FinancialStatement, as_of: date) -> bool:
    if stmt.available_date is None:
        return False
    return stmt.available_date <= as_of


class PitAssessment:
    def __init__(
        self,
        pit_status: PitStatus,
        historical_versions_ok: bool,
        reasons: list[str],
        announcement_coverage: float,
        version_coverage: float,
    ) -> None:
        self.pit_status = pit_status
        self.historical_versions_ok = historical_versions_ok
        self.reasons = reasons
        self.announcement_coverage = announcement_coverage
        self.version_coverage = version_coverage


def assess_pit_capability(
    payload: UniversePayload,
    mode: RunMode,
    *,
    financial_data_mode: FinancialDataMode | str | None = None,
) -> PitAssessment:
    """Inspect whether the dataset can support the requested run mode.

    historical_pit_backtest MUST error-stop if historical versions are missing.
    final_vintage_research always stamps NON_PIT_FINAL_VINTAGE.
    """
    reasons: list[str] = []
    companies = payload.companies
    all_stmts = [s for c in companies for s in c.statements]
    n = len(all_stmts)
    if n == 0:
        reasons.append("No financial statements in the input dataset")
        announcement_cov = 0.0
        version_cov = 0.0
    else:
        dated = sum(
            1
            for s in all_stmts
            if s.available_date is not None or s.announcement_datetime is not None
        )
        versioned = sum(1 for s in all_stmts if s.version_id)
        announcement_cov = dated / n
        version_cov = versioned / n
        if dated < n:
            reasons.append("One or more statements lack announcement_datetime and available_date")
        if versioned < n:
            reasons.append("One or more statements lack version_id")

    missing_prior = _restatements_without_prior_version(companies)
    if missing_prior:
        reasons.append(
            "Restated statements exist without a prior version for the same security/period "
            f"({len(missing_prior)} cases); historical versions are incomplete"
        )

    flagged_final = bool(payload.final_vintage_only) or any(c.final_vintage_only for c in companies)
    hist_flag = payload.historical_versions_available
    if hist_flag is None:
        hist_flag = not flagged_final and not missing_prior and announcement_cov == 1.0 and version_cov == 1.0
        # If any company explicitly says historical versions are unavailable, honour it.
        if any(c.historical_versions_available is False for c in companies):
            hist_flag = False
    if payload.historical_versions_available is False or flagged_final:
        hist_flag = False
        if flagged_final:
            reasons.append("Dataset is flagged final_vintage_only")

    complete = announcement_cov == 1.0 and version_cov == 1.0 and not missing_prior and not flagged_final and bool(hist_flag)

    if mode is RunMode.FINAL_VINTAGE_RESEARCH:
        return PitAssessment(PitStatus.NON_PIT_FINAL_VINTAGE, hist_flag, reasons, announcement_cov, version_cov)

    if mode is RunMode.HISTORICAL_PIT_BACKTEST:
        if financial_data_mode == FinancialDataMode.FINAL_VINTAGE or str(financial_data_mode) == "final_vintage":
            raise InsufficientPitHistoryError(
                "historical_pit_backtest cannot run when financial_data_mode=final_vintage; "
                "refusing to silently fall back to final-vintage data"
            )
        if not complete:
            detail = "; ".join(reasons) if reasons else "historical version metadata is incomplete"
            raise InsufficientPitHistoryError(
                "historical_pit_backtest requires announcement dates, version identifiers, "
                "and historical versions for restated periods; refusing to fall back to "
                f"final-vintage data. Details: {detail}"
            )
        return PitAssessment(PitStatus.STRICT_PIT, True, reasons, announcement_cov, version_cov)

    # current_screen
    pit_status = PitStatus.STRICT_PIT if complete else PitStatus.NON_PIT_FINAL_VINTAGE
    if pit_status is PitStatus.NON_PIT_FINAL_VINTAGE and not reasons:
        reasons.append("Current screen is using non-PIT / final-vintage inputs")
    return PitAssessment(pit_status, complete, reasons, announcement_cov, version_cov)


def _restatements_without_prior_version(companies: list[CompanyInput]) -> list[tuple[str, date, str]]:
    missing: list[tuple[str, date, str]] = []
    for company in companies:
        by_period: dict[date, list[FinancialStatement]] = {}
        for stmt in company.statements:
            by_period.setdefault(stmt.report_period, []).append(stmt)
        for period, stmts in by_period.items():
            if any(s.is_restatement for s in stmts) and len({s.version_id for s in stmts}) < 2:
                # A restatement with only one version means the original was overwritten.
                missing.append((company.security_id, period, stmts[0].statement_type.value))
    return missing


def resolve_as_of_visibility(
    company: CompanyInput,
    as_of: date,
    calendar: TradingCalendar,
    config: ScreenConfig,
) -> list[FinancialStatement]:
    filled = fill_available_dates(company.statements, calendar, config)
    return [s for s in filled if statement_is_visible(s, as_of)]
