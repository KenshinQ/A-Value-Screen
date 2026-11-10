"""13.3 Historical PIT regression around restatements."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from avalue_screen.domain.enums import RunMode
from avalue_screen.domain.errors import InsufficientPitHistoryError
from avalue_screen.ingestion.loaders import load_input
from avalue_screen.pipeline import run_from_paths, screen_universe
from tests.fixtures.generate_fixtures import write_universe


def _ni(result) -> float | None:
    # Prefer annual 2022 net income from series / key metrics / statements via profile key_metrics series
    series = (result.key_metrics or {}).get("series") or {}
    years = (result.key_metrics or {}).get("annual_years") or []
    vals = series.get("net_income_parent") or []
    for y, v in zip(years, vals):
        if y == 2022:
            return v
    # Fallback: look at explain / raw is not exported; use TTM only as last resort
    return None


def _annual_2022_from_features(config, payload, as_of):
    from avalue_screen.pit.availability import fill_available_dates
    from avalue_screen.pit.period_builder import build_period_panel
    from avalue_screen.pit.version_selector import select_visible_statements
    from avalue_screen.utils import TradingCalendar

    company = next(c for c in payload.companies if c.security_id == "RESTATED_DATA")
    calendar = TradingCalendar(payload.trading_days, payload.holidays)
    stmts = fill_available_dates(company.statements, calendar, config)
    visible = select_visible_statements(stmts, as_of)
    panel = build_period_panel(visible, config)
    ann = next((a for a in panel.annuals if a.fiscal_year == 2022), None)
    assert ann is not None
    return ann.values.get("net_income_parent"), ann.version_id


def test_as_of_before_restatement_uses_old_version(config, tmp_path):
    dest = tmp_path / "u"
    write_universe(dest)
    payload = load_input(dest / "universe.json", config)
    ni, version = _annual_2022_from_features(config, payload, date(2023, 6, 30))
    assert version == "v1"
    # Original (pre-cut) 2022 NI is larger than the restated half
    ni_after, version_after = _annual_2022_from_features(config, payload, date(2024, 6, 30))
    assert version_after == "v2"
    assert ni is not None and ni_after is not None
    assert ni_after == pytest.approx(ni * 0.5, rel=1e-9)
    assert ni != ni_after


def test_as_of_after_restatement_uses_new_version(config, tmp_path):
    dest = tmp_path / "u"
    write_universe(dest)
    payload = load_input(dest / "universe.json", config)
    ni, version = _annual_2022_from_features(config, payload, date(2024, 6, 30))
    assert version == "v2"
    assert ni is not None


def test_same_as_of_is_deterministic(config, tmp_path):
    dest = tmp_path / "u"
    write_universe(dest)
    payload = load_input(dest / "universe.json", config)
    a = screen_universe(payload, date(2023, 6, 30), RunMode.HISTORICAL_PIT_BACKTEST, config)
    b = screen_universe(payload, date(2023, 6, 30), RunMode.HISTORICAL_PIT_BACKTEST, config)
    ra = next(r for r in a.results if r.security_id == "RESTATED_DATA")
    rb = next(r for r in b.results if r.security_id == "RESTATED_DATA")
    assert ra.to_profile_dict() == rb.to_profile_dict()


def test_inserting_future_version_does_not_change_past_as_of(config, tmp_path):
    dest = tmp_path / "u"
    write_universe(dest)
    payload = load_input(dest / "universe.json", config)
    before = _annual_2022_from_features(config, payload, date(2023, 6, 30))
    # Adding an even later restatement
    company = next(c for c in payload.companies if c.security_id == "RESTATED_DATA")
    from copy import deepcopy
    from datetime import datetime
    from zoneinfo import ZoneInfo

    extra = []
    for stmt in company.statements:
        if stmt.version_id == "v2" and stmt.report_period.year == 2022:
            cloned = stmt.model_copy(deep=True)
            cloned.version_id = "v3"
            cloned.version_chain = "v3"
            cloned.is_restatement = True
            cloned.available_date = date(2025, 4, 1)
            cloned.announcement_datetime = datetime(2025, 3, 31, 18, tzinfo=ZoneInfo("Asia/Shanghai"))
            if cloned.income.get("net_income_parent"):
                cloned.income["net_income_parent"].value = (cloned.income["net_income_parent"].value or 0) * 0.1
            extra.append(cloned)
    company.statements.extend(extra)
    after = _annual_2022_from_features(config, payload, date(2023, 6, 30))
    assert after == before


def test_historical_mode_fails_on_final_vintage_only(config, tmp_path):
    dest = tmp_path / "u"
    write_universe(dest)
    payload = json.loads((dest / "universe.json").read_text(encoding="utf-8"))
    payload["final_vintage_only"] = True
    payload["historical_versions_available"] = False
    # Strip versions from a company to simulate final-vintage overwrite
    for c in payload["companies"]:
        c["final_vintage_only"] = True
        c["historical_versions_available"] = False
        # keep only latest restated 2022 if present, drop v1
        if c["security_id"] == "RESTATED_DATA":
            c["statements"] = [s for s in c["statements"] if s.get("version_id") != "v1" or s.get("report_period", "").startswith("2021") is False]
            # drop original v1 for 2022
            c["statements"] = [
                s for s in c["statements"]
                if not (str(s.get("report_period", "")).startswith("2022") and s.get("version_id") == "v1")
            ]
    path = dest / "final_only.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InsufficientPitHistoryError):
        run_from_paths(
            input_path=path,
            output_dir=tmp_path / "out",
            as_of=date(2023, 6, 30),
            mode=RunMode.HISTORICAL_PIT_BACKTEST,
            config_path=Path(__file__).resolve().parents[2] / "configs" / "default.yaml",
        )


def test_final_vintage_research_stamps_non_pit(config, tmp_path):
    dest = tmp_path / "u"
    write_universe(dest)
    result = run_from_paths(
        input_path=dest / "universe.json",
        output_dir=tmp_path / "out",
        as_of=date(2026, 11, 10),
        mode=RunMode.FINAL_VINTAGE_RESEARCH,
        config_path=Path(__file__).resolve().parents[2] / "configs" / "default.yaml",
    )
    assert result.pit_status.value == "NON_PIT_FINAL_VINTAGE"
    manifest = json.loads((tmp_path / "out" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pit_status"] == "NON_PIT_FINAL_VINTAGE"
    assert "tradable" in manifest["disclaimer"].lower() or "look-ahead" in manifest["disclaimer"].lower()
    for r in result.results:
        assert r.pit_status.value == "NON_PIT_FINAL_VINTAGE"
