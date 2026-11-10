"""Enumerations used across A-Value-Screen."""

from __future__ import annotations

from enum import Enum


class RunMode(str, Enum):
    CURRENT_SCREEN = "current_screen"
    HISTORICAL_PIT_BACKTEST = "historical_pit_backtest"
    FINAL_VINTAGE_RESEARCH = "final_vintage_research"


class FinancialDataMode(str, Enum):
    STRICT_PIT = "strict_pit"
    FINAL_VINTAGE = "final_vintage"


class PitStatus(str, Enum):
    STRICT_PIT = "STRICT_PIT"
    NON_PIT_FINAL_VINTAGE = "NON_PIT_FINAL_VINTAGE"


class EligibilityStatus(str, Enum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECT = "REJECT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Grade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    REVIEW = "REVIEW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class DataQualityGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    F = "F"


class StatementType(str, Enum):
    ANNUAL = "ANNUAL"
    Q1 = "Q1"
    H1 = "H1"
    Q2 = "Q2"  # treated as H1 cumulative when source uses Q2
    Q3 = "Q3"
    Q4 = "Q4"
    FY = "FY"

    def canonical(self) -> "StatementType":
        if self in (StatementType.FY, StatementType.Q4, StatementType.ANNUAL):
            return StatementType.ANNUAL
        if self in (StatementType.Q2, StatementType.H1):
            return StatementType.H1
        return self

    def is_annual(self) -> bool:
        return self.canonical() is StatementType.ANNUAL

    def fiscal_quarter_end(self) -> int:
        """Return 1..4 for the period end quarter."""
        c = self.canonical()
        return {
            StatementType.Q1: 1,
            StatementType.H1: 2,
            StatementType.Q3: 3,
            StatementType.ANNUAL: 4,
        }[c]


class StatementBlock(str, Enum):
    INCOME = "income"
    BALANCE = "balance"
    CASHFLOW = "cashflow"
    MARKET = "market"
    OTHER = "other"


class AuditOpinion(str, Enum):
    UNQUALIFIED = "UNQUALIFIED"
    QUALIFIED = "QUALIFIED"  # 保留意见
    ADVERSE = "ADVERSE"  # 否定意见
    DISCLAIMER = "DISCLAIMER"  # 无法表示意见
    EMPHASIS = "EMPHASIS"  # 强调事项（无保留+强调）
    UNKNOWN = "UNKNOWN"


class MispricingCategory(str, Enum):
    TEMPORARY_SHOCK = "TEMPORARY_SHOCK"
    MARKET_SENTIMENT = "MARKET_SENTIMENT"
    CYCLICAL_DOWNTURN = "CYCLICAL_DOWNTURN"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    STRUCTURAL_DECLINE = "STRUCTURAL_DECLINE"


class EvidenceGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class EvidenceReviewStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class ImpairmentAnswer(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


class ReverseDcfStatus(str, Enum):
    SOLVED = "SOLVED"
    BELOW_SEARCH_RANGE = "BELOW_SEARCH_RANGE"
    ABOVE_SEARCH_RANGE = "ABOVE_SEARCH_RANGE"
    UNAVAILABLE_NONPOSITIVE_FCF = "REVERSE_DCF_UNAVAILABLE_NONPOSITIVE_FCF"
    UNAVAILABLE_SIGN_CHANGE = "REVERSE_DCF_UNAVAILABLE_SIGN_CHANGE"
    UNAVAILABLE = "UNAVAILABLE"


class MetricStatus(str, Enum):
    OK = "OK"
    INVALID = "INVALID"
    ESTIMATED = "ESTIMATED"
    NA_NO_INTEREST = "NA_NO_INTEREST"
    NA_NEGATIVE_DENOMINATOR = "NA_NEGATIVE_DENOMINATOR"
    NA_NONPOSITIVE_CAPITAL = "NA_NONPOSITIVE_CAPITAL"
    NA_UNINTERPRETABLE_RATIO = "NA_UNINTERPRETABLE_RATIO"
    TURNAROUND = "TURNAROUND"
    MISSING = "MISSING"
    TAX_RATE_FALLBACK = "TAX_RATE_FALLBACK"
    COST_OF_DEBT_FALLBACK = "COST_OF_DEBT_FALLBACK"
    WACC_CLAMPED = "WACC_CLAMPED"
    TERMINAL_VALUE_DOMINANT = "TERMINAL_VALUE_DOMINANT"
    BETA_FALLBACK = "BETA_FALLBACK"
    SHARES_FALLBACK = "SHARES_FALLBACK"
    NET_CASH = "NET_CASH"
