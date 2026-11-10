"""CLI: avalue-screen run | avalue-screen explain."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from avalue_screen.domain.enums import RunMode
from avalue_screen.domain.errors import InsufficientPitHistoryError
from avalue_screen.pipeline import run_from_paths
from avalue_screen.utils import parse_date


@click.group()
def main() -> None:
    """A-Value-Screen: A-share quantitative fundamental screening engine."""


@main.command("run")
@click.option("--as-of", "as_of", required=True, help="Screening date YYYY-MM-DD")
@click.option(
    "--mode",
    type=click.Choice([m.value for m in RunMode]),
    required=True,
    help="current_screen | historical_pit_backtest | final_vintage_research",
)
@click.option("--config", "config_path", default="configs/default.yaml", show_default=True)
@click.option("--input", "input_path", required=True, help="JSON file or directory of fixtures / CSVs")
@click.option("--output", "output_dir", required=True, help="Output directory")
@click.option("--mapping", "mapping_path", default=None, help="Optional field-mapping YAML")
@click.option(
    "--financial-data-mode",
    default=None,
    type=click.Choice(["strict_pit", "final_vintage"]),
    help="Override financial data mode. historical_pit_backtest cannot use final_vintage.",
)
def run_cmd(
    as_of: str,
    mode: str,
    config_path: str,
    input_path: str,
    output_dir: str,
    mapping_path: str | None,
    financial_data_mode: str | None,
) -> None:
    if parse_date(as_of) is None:
        raise click.ClickException(f"Invalid as-of date: {as_of}")
    try:
        result = run_from_paths(
            input_path=input_path,
            output_dir=output_dir,
            as_of=as_of,
            mode=mode,
            config_path=config_path if Path(config_path).exists() else None,
            mapping_path=mapping_path,
            financial_data_mode=financial_data_mode,
        )
    except InsufficientPitHistoryError as exc:
        raise click.ClickException(str(exc)) from exc
    n = len(result.results)
    n_pass = sum(1 for r in result.results if r.eligibility_status.value == "PASS")
    n_cand = sum(1 for r in result.results if r.candidate_eligible)
    click.echo(
        f"Wrote {n} company results to {output_dir} "
        f"(mode={result.mode.value}, pit_status={result.pit_status.value}, "
        f"PASS={n_pass}, official_candidates={n_cand})"
    )
    if result.mode is RunMode.FINAL_VINTAGE_RESEARCH or result.pit_status.value == "NON_PIT_FINAL_VINTAGE":
        click.echo(
            "NOTE: pit_status=NON_PIT_FINAL_VINTAGE. Do not treat this run as a tradable "
            "historical backtest or as free of look-ahead bias."
        )


@main.command("explain")
@click.option("--symbol", required=True, help="Ticker, e.g. 600001.SH")
@click.option("--as-of", "as_of", required=True)
@click.option("--input", "input_path", required=True, help="Same input used for the run, or a run output directory")
@click.option("--config", "config_path", default="configs/default.yaml", show_default=True)
@click.option(
    "--mode",
    type=click.Choice([m.value for m in RunMode]),
    default=RunMode.CURRENT_SCREEN.value,
    show_default=True,
)
@click.option("--output", "output_dir", default=None, help="Optional directory to write the profile JSON")
def explain_cmd(
    symbol: str,
    as_of: str,
    input_path: str,
    config_path: str,
    mode: str,
    output_dir: str | None,
) -> None:
    """Print the explainability profile for a single symbol."""
    path = Path(input_path)
    profile = path / "company_profiles" / f"{symbol}.json"
    if profile.exists():
        data = json.loads(profile.read_text(encoding="utf-8"))
    else:
        try:
            tmp_out = Path(output_dir) if output_dir else Path(input_path) / "_explain_tmp"
            result = run_from_paths(
                input_path=input_path,
                output_dir=tmp_out,
                as_of=as_of,
                mode=mode,
                config_path=config_path if Path(config_path).exists() else None,
            )
        except InsufficientPitHistoryError as exc:
            raise click.ClickException(str(exc)) from exc
        match = next((r for r in result.results if r.symbol == symbol or r.security_id == symbol), None)
        if match is None:
            raise click.ClickException(f"Symbol {symbol} not found in the input universe")
        data = match.to_profile_dict()
    click.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
