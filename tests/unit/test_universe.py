"""Universe filters: ordinary A-shares, BSE toggle, main_board_only, financials, ST."""

from datetime import date

from avalue_screen.domain.models import CompanyInput, MarketSnapshot
from avalue_screen.screening.universe import classify_board, classify_security


def _co(symbol, exchange, **kwargs):
    market = MarketSnapshot(
        listing_date=kwargs.pop("listing_date", date(2010, 1, 1)),
        is_financial=kwargs.pop("is_financial", False),
        industry=kwargs.pop("industry", "manufacturing"),
        sector=kwargs.pop("sector", "industrials"),
        risk_warning_flag=kwargs.pop("risk_warning_flag", False),
        delisting_date=kwargs.pop("delisting_date", None),
        suspension_flag=kwargs.pop("suspension_flag", False),
    )
    return CompanyInput(security_id=symbol, symbol=symbol, exchange=exchange, market=market, **kwargs)


def test_board_classification(config):
    assert classify_board(_co("600001.SH", "SH"), config) == "main"
    assert classify_board(_co("000001.SZ", "SZ"), config) == "main"
    assert classify_board(_co("002001.SZ", "SZ"), config) == "main"
    assert classify_board(_co("300001.SZ", "SZ"), config) == "chi_next"
    assert classify_board(_co("688001.SH", "SH"), config) == "star"
    assert classify_board(_co("830001.BJ", "BJ"), config) == "bse"
    assert classify_board(_co("900001.SH", "SH"), config) == "non_ordinary"


def test_main_board_only_drops_chinext_star_bse(config):
    as_of = date(2026, 11, 10)
    # Project default is main_board_only=true
    assert classify_security(_co("600001.SH", "SH"), as_of, config).included is True
    assert classify_security(_co("300001.SZ", "SZ"), as_of, config).board == "chi_next"
    assert classify_security(_co("300001.SZ", "SZ"), as_of, config).included is False
    assert classify_security(_co("688001.SH", "SH"), as_of, config).included is False
    assert classify_security(_co("830001.BJ", "BJ"), as_of, config).included is False


def test_all_board_universe_includes_chinext_when_flag_off(config):
    as_of = date(2026, 11, 10)
    data = config.to_dict()
    data["universe"]["main_board_only"] = False
    from avalue_screen.config import ScreenConfig

    cfg2 = ScreenConfig(data)
    assert classify_security(_co("300001.SZ", "SZ"), as_of, cfg2).included is True
    assert classify_security(_co("688001.SH", "SH"), as_of, cfg2).included is True
    assert classify_security(_co("830001.BJ", "BJ"), as_of, cfg2).included is True


def test_bse_can_be_excluded(config):
    data = config.to_dict()
    data["universe"]["include_bse"] = False
    from avalue_screen.config import ScreenConfig

    cfg2 = ScreenConfig(data)
    dec = classify_security(_co("830001.BJ", "BJ"), date(2026, 11, 10), cfg2)
    assert dec.included is False


def test_financials_excluded(config):
    dec = classify_security(_co("600000.SH", "SH", is_financial=True, industry="银行"), date(2026, 11, 10), config)
    assert dec.included is False
    assert dec.is_financial is True


def test_historical_delisting_kept_if_still_listed_at_as_of(config):
    # Later delisting must not remove the name from a historical universe
    co = _co("600010.SH", "SH", delisting_date=date(2025, 6, 1))
    dec_hist = classify_security(co, date(2024, 12, 31), config)
    assert dec_hist.included is True
    dec_after = classify_security(co, date(2025, 12, 31), config)
    assert dec_after.included is False
