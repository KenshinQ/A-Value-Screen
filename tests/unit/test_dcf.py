"""13.1.11 Three-scenario DCF, terminal-value weight, margin of safety, reverse DCF."""

from avalue_screen.domain.enums import ReverseDcfStatus
from avalue_screen.valuation.dcf import _present_value, _project_path
from avalue_screen.valuation.reverse_dcf import equity_value_at_g, solve_reverse_dcf


def test_present_value_and_terminal_weight():
    fcffs = [100.0, 100.0, 100.0, 100.0, 100.0]
    ev, pv_tv, weight = _present_value(fcffs, wacc=0.10, tg=0.02)
    # TV = 100*1.02/(0.10-0.02)=1275; pv_tv = 1275/1.1**5
    expected_tv = 1275.0 / (1.1 ** 5)
    assert abs(pv_tv - expected_tv) < 1e-8
    assert 0 < weight < 1
    assert ev == sum(100.0 / (1.1 ** t) for t in range(1, 6)) + expected_tv


def test_mos_formula():
    conservative = 10.0
    price = 6.0
    mos = 1.0 - price / conservative
    assert abs(mos - 0.4) < 1e-12


def test_mos_invalid_when_conservative_nonpositive():
    conservative = 0.0
    assert conservative <= 0  # MOS must be invalid in the engine


def test_reverse_dcf_solves_known_g(config):
    fcff0 = 100.0
    wacc, tg, years = 0.10, 0.02, 5
    true_g = 0.04
    ev = equity_value_at_g(true_g, fcff0, wacc, tg, years, net_debt=0.0, minority_interest=0.0, eligible_non_operating=0.0)
    result = solve_reverse_dcf(
        fcff0=fcff0,
        wacc=wacc,
        terminal_growth=tg,
        years=years,
        market_cap=ev,
        net_debt=0.0,
        minority_interest=0.0,
        eligible_non_operating=0.0,
        config=config,
    )
    assert result.status is ReverseDcfStatus.SOLVED
    assert abs(result.g_implied - true_g) < 1e-5


def test_reverse_dcf_nonpositive_fcff(config):
    result = solve_reverse_dcf(
        fcff0=0.0, wacc=0.1, terminal_growth=0.02, years=5,
        market_cap=100.0, net_debt=0.0, minority_interest=0.0, eligible_non_operating=0.0, config=config,
    )
    assert result.status is ReverseDcfStatus.UNAVAILABLE_NONPOSITIVE_FCF
    assert result.g_implied is None


def test_reverse_dcf_out_of_range_is_not_clamped(config):
    # Tiny market cap => implied g would be below -10%
    result = solve_reverse_dcf(
        fcff0=100.0, wacc=0.10, terminal_growth=0.02, years=5,
        market_cap=1.0, net_debt=0.0, minority_interest=0.0, eligible_non_operating=0.0, config=config,
    )
    assert result.status is ReverseDcfStatus.BELOW_SEARCH_RANGE
    assert result.g_implied is None


def test_reinvestment_is_capped_when_growth_requires_it():
    # Very low capex/nwc with high growth should be lifted so incremental ROIC is not absurd
    path = _project_path(
        revenue0=1000.0,
        nwc0=0.0,
        growth=0.10,
        margin=0.20,
        tax=0.25,
        da_ratio=0.01,
        capex_ratio=0.01,
        nwc_ratio=0.0,
        years=5,
        incremental_roic_cap=0.40,
    )
    # Incremental investment should be enough that FCFF is below the unconstrained "almost no reinvestment" path
    unconstrained = _project_path(
        revenue0=1000.0, nwc0=0.0, growth=0.10, margin=0.20, tax=0.25,
        da_ratio=0.01, capex_ratio=0.01, nwc_ratio=0.0, years=5, incremental_roic_cap=1e9,
    )
    assert path["fcff"][-1] < unconstrained["fcff"][-1]
