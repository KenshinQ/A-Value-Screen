"""CSV batch reports required by SPEC 11.2."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Sequence

from avalue_screen.domain.enums import EligibilityStatus
from avalue_screen.domain.models import CompanyResult
from avalue_screen.screening.ranking import official_candidates, rank_results


SUMMARY_FIELDS = [
    "rank",
    "security_id",
    "symbol",
    "company_name",
    "exchange",
    "industry",
    "eligibility_status",
    "grade",
    "total_score",
    "commercial_quality",
    "financial_health",
    "earnings_quality",
    "valuation_attractiveness",
    "mispricing_evidence",
    "data_quality_grade",
    "pit_status",
    "margin_of_safety",
    "conservative_value_per_share",
    "current_price",
    "listing_status",
    "liquidity_ok",
    "daily_turnover_cny",
    "candidate_eligible",
    "rejection_rule_ids",
    "review_rule_ids",
]


def _row(result: CompanyResult, rank: int | None = None) -> dict[str, Any]:
    scores = result.scores or {}
    val = result.valuation or {}
    return {
        "rank": rank,
        "security_id": result.security_id,
        "symbol": result.symbol,
        "company_name": result.company_name,
        "exchange": result.exchange,
        "industry": result.industry,
        "eligibility_status": result.eligibility_status.value,
        "grade": scores.get("grade"),
        "total_score": scores.get("total"),
        "commercial_quality": scores.get("commercial_quality"),
        "financial_health": scores.get("financial_health"),
        "earnings_quality": scores.get("earnings_quality"),
        "valuation_attractiveness": scores.get("valuation_attractiveness"),
        "mispricing_evidence": scores.get("mispricing_evidence"),
        "data_quality_grade": result.data_quality_grade.value,
        "pit_status": result.pit_status.value,
        "margin_of_safety": val.get("margin_of_safety"),
        "conservative_value_per_share": val.get("conservative_value_per_share"),
        "current_price": val.get("current_price"),
        "listing_status": result.listing_status,
        "liquidity_ok": result.liquidity_ok,
        "daily_turnover_cny": result.daily_turnover_cny,
        "candidate_eligible": result.candidate_eligible,
        "rejection_rule_ids": ";".join(h.rule_id for h in result.rejection_reasons),
        "review_rule_ids": ";".join(h.rule_id for h in result.review_flags),
        "insufficient_rule_ids": ";".join(h.rule_id for h in result.insufficient_reasons),
    }


def _write(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_csv_reports(results: Sequence[CompanyResult], output_dir: str | Path) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ranked = rank_results(list(results))
    summary_rows = [_row(r, i) for i, r in enumerate(ranked, start=1)]
    _write(output_dir / "screen_summary.csv", summary_rows, SUMMARY_FIELDS)

    cands = official_candidates(ranked)
    cand_fields = SUMMARY_FIELDS + ["positive_drivers"]
    cand_rows = []
    for i, r in enumerate(cands, start=1):
        row = _row(r, i)
        row["positive_drivers"] = " | ".join((r.explainability or {}).get("positive_drivers") or [])
        cand_rows.append(row)
    _write(output_dir / "candidate_list.csv", cand_rows, cand_fields)

    rejected = [r for r in ranked if r.eligibility_status is EligibilityStatus.REJECT]
    rej_fields = ["security_id", "symbol", "company_name", "rule_id", "reason"]
    rej_rows = []
    for r in rejected:
        for h in r.rejection_reasons:
            rej_rows.append(
                {"security_id": r.security_id, "symbol": r.symbol, "company_name": r.company_name, "rule_id": h.rule_id, "reason": h.reason}
            )
    _write(output_dir / "rejected_list.csv", rej_rows, rej_fields)

    reviews = [r for r in ranked if r.eligibility_status is EligibilityStatus.REVIEW_REQUIRED]
    rev_fields = ["security_id", "symbol", "company_name", "rule_id", "reason", "evidence_gap"]
    rev_rows = []
    for r in reviews:
        for h in r.review_flags:
            rev_rows.append(
                {
                    "security_id": r.security_id,
                    "symbol": r.symbol,
                    "company_name": r.company_name,
                    "rule_id": h.rule_id,
                    "reason": h.reason,
                    "evidence_gap": (r.explainability or {}).get("missing_data"),
                }
            )
    _write(output_dir / "review_queue.csv", rev_rows, rev_fields)

    insuff = [r for r in ranked if r.eligibility_status is EligibilityStatus.INSUFFICIENT_DATA]
    ins_fields = ["security_id", "symbol", "company_name", "rule_id", "reason", "missing_fields"]
    ins_rows = []
    for r in insuff:
        missing = ";".join((r.explainability or {}).get("missing_data") or [])
        hits = r.insufficient_reasons or [None]
        for h in hits:
            ins_rows.append(
                {
                    "security_id": r.security_id,
                    "symbol": r.symbol,
                    "company_name": r.company_name,
                    "rule_id": h.rule_id if h else "",
                    "reason": h.reason if h else "insufficient data",
                    "missing_fields": missing,
                }
            )
    _write(output_dir / "insufficient_data.csv", ins_rows, ins_fields)

    return {
        "screen_summary.csv": output_dir / "screen_summary.csv",
        "candidate_list.csv": output_dir / "candidate_list.csv",
        "rejected_list.csv": output_dir / "rejected_list.csv",
        "review_queue.csv": output_dir / "review_queue.csv",
        "insufficient_data.csv": output_dir / "insufficient_data.csv",
    }
