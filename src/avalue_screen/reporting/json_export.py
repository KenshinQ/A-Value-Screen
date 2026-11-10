"""JSON company profiles, data-quality report, and run manifest."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from avalue_screen import __version__
from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import RunMode, PitStatus
from avalue_screen.domain.models import CompanyResult
from avalue_screen.utils import sha256_file, stable_hash, SHANGHAI


def _dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")


def write_json_reports(
    results: Sequence[CompanyResult],
    output_dir: str | Path,
    data_quality_bundle: dict[str, Any],
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    profiles = output_dir / "company_profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for r in results:
        path = profiles / f"{r.symbol}.json"
        _dump(path, r.to_profile_dict())
        written[r.symbol] = path
    dq_path = output_dir / "data_quality_report.json"
    _dump(dq_path, data_quality_bundle)
    written["data_quality_report.json"] = dq_path
    return written


def write_run_manifest(
    output_dir: str | Path,
    *,
    config: ScreenConfig,
    as_of: date,
    mode: RunMode,
    pit_status: PitStatus,
    input_path: str | Path | None,
    input_hashes: dict[str, str],
    extra: dict[str, Any] | None = None,
) -> Path:
    output_dir = Path(output_dir)
    now = datetime.now(SHANGHAI)
    manifest = {
        "code_version": __version__,
        "rule_version": config.rule_version,
        "config_source": config.source_path,
        "config_hash": stable_hash(config.to_dict()),
        "as_of_date": as_of.isoformat(),
        "run_mode": mode.value,
        "pit_status": pit_status.value,
        "run_time": now.isoformat(),
        "timezone": "Asia/Shanghai",
        "input_path": str(input_path) if input_path else None,
        "input_file_hashes": input_hashes,
        "notes": extra or {},
    }
    if mode is RunMode.FINAL_VINTAGE_RESEARCH or pit_status is PitStatus.NON_PIT_FINAL_VINTAGE:
        manifest["disclaimer"] = (
            "pit_status=NON_PIT_FINAL_VINTAGE. This run must not be described as a tradable "
            "historical backtest or as free of look-ahead bias. Historical financial items may "
            "have been restated after the as-of date."
        )
    path = output_dir / "run_manifest.json"
    _dump(path, manifest)
    return path


def hash_input_tree(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    if p.is_file():
        return {p.name: sha256_file(p)}
    out: dict[str, str] = {}
    for f in sorted(p.rglob("*")):
        if f.is_file() and f.suffix.lower() in {".json", ".csv", ".yaml", ".yml", ".tsv"}:
            out[str(f.relative_to(p))] = sha256_file(f)
    return out
