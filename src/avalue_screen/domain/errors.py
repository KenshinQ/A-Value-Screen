"""Typed errors for A-Value-Screen."""

from __future__ import annotations


class AValueScreenError(Exception):
    """Base error."""


class ConfigError(AValueScreenError):
    """Invalid or incomplete configuration."""


class UnitNormalizationError(AValueScreenError):
    """Currency or unit cannot be reconciled; computation must stop."""


class InsufficientPitHistoryError(AValueScreenError):
    """historical_pit_backtest cannot run because historical versions are missing.

    This error must stop the run. Callers must not fall back to final-vintage data.
    """


class DataContractError(AValueScreenError):
    """Input does not satisfy the data contract."""


class TradingCalendarError(AValueScreenError):
    """Trading calendar cannot resolve a required date."""
