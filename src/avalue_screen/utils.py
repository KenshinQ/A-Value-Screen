"""Pure helpers: dates, scoring bands, ratios, CAGR, OLS, hashing."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from avalue_screen.domain.errors import TradingCalendarError
from avalue_screen.domain.enums import MetricStatus


SHANGHAI = ZoneInfo("Asia/Shanghai")


def parse_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        return parse_datetime(text).date()
    return date.fromisoformat(text[:10])


def parse_datetime(value: datetime | date | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=SHANGHAI)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return datetime.fromisoformat(text[:10]).replace(tzinfo=SHANGHAI)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI)
    return dt


def has_time_precision(value: datetime | str | None) -> bool:
    """True when the announcement includes a clock time, not just a calendar date."""
    if value is None:
        return False
    if isinstance(value, datetime):
        return not (value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0)
    text = str(value)
    return "T" in text or " " in text[10:] if len(text) > 10 else False


def to_shanghai(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SHANGHAI)
    return dt.astimezone(SHANGHAI)


def parse_hhmm(value: str) -> time:
    parts = value.split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return time(hour, minute)


def is_finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def safe_div(numer: float | None, denom: float | None) -> float | None:
    if not is_finite(numer) or not is_finite(denom) or denom == 0:
        return None
    return numer / denom


def cagr(start: float | None, end: float | None, years: float) -> tuple[float | None, MetricStatus]:
    if years <= 0:
        return None, MetricStatus.INVALID
    if not is_finite(start) or not is_finite(end) or start is None or end is None:
        return None, MetricStatus.MISSING
    if start <= 0 or end <= 0:
        return None, MetricStatus.TURNAROUND
    return (end / start) ** (1.0 / years) - 1.0, MetricStatus.OK


def ols_slope(values: Sequence[float | None]) -> float | None:
    """OLS slope of value vs index 0..n-1, skipping None. Needs at least 2 points."""
    xs: list[float] = []
    ys: list[float] = []
    for i, v in enumerate(values):
        if is_finite(v):
            xs.append(float(i))
            ys.append(float(v))  # type: ignore[arg-type]
    n = len(xs)
    if n < 2:
        return None
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    var_x = sum((x - x_mean) ** 2 for x in xs)
    if var_x == 0:
        return 0.0
    cov = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    return cov / var_x


def median(values: Iterable[float | None]) -> float | None:
    nums = sorted(v for v in values if is_finite(v))
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2 == 1:
        return nums[mid]
    return (nums[mid - 1] + nums[mid]) / 2.0


def mean(values: Iterable[float | None]) -> float | None:
    nums = [v for v in values if is_finite(v)]
    if not nums:
        return None
    return sum(nums) / len(nums)


def stdev(values: Iterable[float | None]) -> float | None:
    nums = [v for v in values if is_finite(v)]
    if len(nums) < 2:
        return 0.0 if len(nums) == 1 else None
    m = sum(nums) / len(nums)
    var = sum((v - m) ** 2 for v in nums) / (len(nums) - 1)
    return math.sqrt(var)


def winsorize(values: Sequence[float], lower_p: float, upper_p: float) -> list[float]:
    """Return winsorized copies; never mutates or replaces the raw series used for official metrics."""
    if not values:
        return []
    ordered = sorted(values)
    n = len(ordered)

    def pct(p: float) -> float:
        if n == 1:
            return ordered[0]
        idx = min(n - 1, max(0, int(round(p * (n - 1)))))
        return ordered[idx]

    lo = pct(lower_p)
    hi = pct(upper_p)
    return [min(max(v, lo), hi) for v in values]


def within_tolerance(lhs: float, rhs: float, *, abs_tol: float, rel_tol: float) -> bool:
    return abs(lhs - rhs) <= max(abs_tol, rel_tol * max(abs(lhs), abs(rhs)))


def score_from_bands(value: float | None, bands: Sequence[Mapping[str, Any]]) -> tuple[int | None, Mapping[str, Any] | None]:
    """Apply left-closed right-open bands. Highest band (max is None) includes its lower bound and above.

    A band with min=None is the lowest remainder bucket (value < max).
    A band with max=None is the highest bucket (value >= min).
    Finite bands match min <= value < max.
    """
    if value is None or not is_finite(value):
        return None, None
    # Try highest (max is None) first, then finite, then lowest (min is None)
    highest = [b for b in bands if b.get("max") is None and b.get("min") is not None]
    finite = [b for b in bands if b.get("min") is not None and b.get("max") is not None]
    lowest = [b for b in bands if b.get("min") is None]
    for band in highest:
        if value >= float(band["min"]):
            return int(band["score"]), band
    # finite bands: check from high to low to be deterministic
    finite_sorted = sorted(finite, key=lambda b: float(b["min"]), reverse=True)
    for band in finite_sorted:
        lo = float(band["min"])
        hi = float(band["max"])
        if lo <= value < hi:
            return int(band["score"]), band
    for band in lowest:
        hi = band.get("max")
        if hi is None or value < float(hi):
            return int(band["score"]), band
    return None, None


def stable_hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


from pathlib import Path as _Path


def sha256_file(path: str | _Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TradingCalendar:
    """Simple trading calendar. Official exchange calendars may be supplied as an explicit day list."""

    def __init__(
        self,
        trading_days: Sequence[date] | None = None,
        holidays: Sequence[date] | None = None,
    ) -> None:
        self._explicit = sorted({parse_date(d) for d in trading_days or [] if parse_date(d)})
        self._holidays = {parse_date(d) for d in holidays or [] if parse_date(d)}
        self._explicit_set = set(self._explicit)

    def is_trading_day(self, d: date) -> bool:
        if self._explicit:
            return d in self._explicit_set
        return d.weekday() < 5 and d not in self._holidays

    def next_trading_day(self, d: date) -> date:
        """First trading day strictly after d."""
        cursor = d + timedelta(days=1)
        for _ in range(400):
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)
        raise TradingCalendarError(f"Cannot find a trading day after {d}")

    def trading_day_or_next(self, d: date) -> date:
        if self.is_trading_day(d):
            return d
        return self.next_trading_day(d)

    def previous_trading_day(self, d: date) -> date:
        cursor = d - timedelta(days=1)
        for _ in range(400):
            if self.is_trading_day(cursor):
                return cursor
            cursor -= timedelta(days=1)
        raise TradingCalendarError(f"Cannot find a trading day before {d}")

    def session_on_or_before(self, d: date) -> date | None:
        if self.is_trading_day(d):
            return d
        try:
            return self.previous_trading_day(d)
        except TradingCalendarError:
            return None

    def days_between(self, start: date, end: date) -> list[date]:
        """Inclusive trading days from start to end."""
        if self._explicit:
            return [x for x in self._explicit if start <= x <= end]
        out: list[date] = []
        cursor = start
        while cursor <= end:
            if self.is_trading_day(cursor):
                out.append(cursor)
            cursor += timedelta(days=1)
        return out


def year_fraction(start: date, end: date, basis: float = 365.25) -> float:
    return (end - start).days / basis
