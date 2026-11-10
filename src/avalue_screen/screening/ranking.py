"""Official ranking: PASS first, then grade, MOS, commercial quality, data-quality grade.

REVIEW_REQUIRED and INSUFFICIENT_DATA must not appear on the official candidate list.
"""

from __future__ import annotations

from typing import Any, Sequence

from avalue_screen.domain.enums import EligibilityStatus, Grade, DataQualityGrade
from avalue_screen.domain.models import CompanyResult

_GRADE_RANK = {Grade.A.value: 0, "A": 0, Grade.B.value: 1, "B": 1, Grade.C.value: 2, "C": 2, Grade.D.value: 3, "D": 3, "REVIEW": 4, "INSUFFICIENT_DATA": 5}
_DQ_RANK = {"A": 0, "B": 1, "C": 2, "F": 3}


def _sort_key(row: CompanyResult) -> tuple:
    scores = row.scores or {}
    mos = (row.valuation or {}).get("margin_of_safety")
    # Higher MOS first: sort by negative MOS. Missing MOS sorts last.
    mos_key = -mos if isinstance(mos, (int, float)) else 1e9
    cq = scores.get("commercial_quality")
    cq_key = -(cq if isinstance(cq, (int, float)) else -1)
    return (
        0 if row.eligibility_status is EligibilityStatus.PASS else 1,
        _GRADE_RANK.get(str(scores.get("grade")), 9),
        mos_key,
        cq_key,
        _DQ_RANK.get(str(row.data_quality_grade.value if hasattr(row.data_quality_grade, "value") else row.data_quality_grade), 9),
        row.symbol,
    )


def rank_results(results: Sequence[CompanyResult]) -> list[CompanyResult]:
    return sorted(results, key=_sort_key)


def official_candidates(results: Sequence[CompanyResult]) -> list[CompanyResult]:
    """PASS names that cleared universe + listing + liquidity candidate flags."""
    ranked = rank_results(results)
    out = []
    for r in ranked:
        if r.eligibility_status is not EligibilityStatus.PASS:
            continue
        if r.scores.get("grade") in {"REVIEW", "INSUFFICIENT_DATA"}:
            continue
        if not r.candidate_eligible:
            continue
        out.append(r)
    return out
