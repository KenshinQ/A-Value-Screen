"""Structured evidence only. Unverified text cannot support an A-grade conclusion."""

from __future__ import annotations

from datetime import date
from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.enums import EvidenceGrade, EvidenceReviewStatus, MispricingCategory
from avalue_screen.domain.models import CompanyInput, EvidenceRecord


def _visible(ev: EvidenceRecord, as_of: date) -> bool:
    if ev.published_at and ev.published_at > as_of:
        return False
    if ev.reviewed_at and ev.review_status is EvidenceReviewStatus.CONFIRMED and ev.reviewed_at > as_of:
        # Confirmation after as_of is not visible in a historical PIT run; keep the record as unconfirmed.
        return True
    return True


def evaluate_evidence(
    company: CompanyInput,
    as_of: date,
    config: ScreenConfig,
    *,
    reverse_dcf_gap: float | None = None,
    divergence: dict[str, Any] | None = None,
    impairment_state: str | None = None,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    confirmed_grades = {str(x) for x in config.get("mispricing.confirmed_evidence_grades", ["A", "B"])}
    visible = [e for e in company.evidence if _visible(e, as_of)]
    # Hide confirmations that occurred after as_of
    adjusted: list[EvidenceRecord] = []
    for ev in visible:
        if ev.review_status is EvidenceReviewStatus.CONFIRMED and ev.reviewed_at and ev.reviewed_at > as_of:
            adjusted.append(ev.model_copy(update={"review_status": EvidenceReviewStatus.PENDING}))
        else:
            adjusted.append(ev)

    confirmed = [
        e
        for e in adjusted
        if e.review_status is EvidenceReviewStatus.CONFIRMED
        and e.evidence_grade.value in confirmed_grades
    ]
    pending = [e for e in adjusted if e.review_status is EvidenceReviewStatus.PENDING]
    supports = [e for e in confirmed if e.supports_reversibility is True]
    against = [e for e in confirmed if e.supports_reversibility is False]
    media_only = all(
        "media" in (e.source_name or "").lower() or e.evidence_grade is EvidenceGrade.C
        for e in confirmed
    ) if confirmed else False

    cats = []
    for e in adjusted:
        cat = e.category.value if hasattr(e.category, "value") else str(e.category)
        if cat and cat not in cats:
            cats.append(cat)
    if categories:
        for c in categories:
            if c not in cats:
                cats.append(c)

    # Reversibility state
    if impairment_state == "confirmed_impairment" or MispricingCategory.STRUCTURAL_DECLINE.value in cats:
        rev_state = "structural"
    elif supports and not media_only:
        rev_state = "confirmed_temporary"
    elif pending or (confirmed and media_only) or (any(e.supports_reversibility for e in pending)):
        rev_state = "plausible_insufficient"
    elif against:
        rev_state = "structural"
    else:
        # No evidence at all: cannot claim reversibility
        rev_state = "structural" if impairment_state == "confirmed_impairment" else "plausible_insufficient" if pending else "structural"

    # If there is simply no evidence and no confirmed structural issue, reversibility is not confirmed.
    # Conservatively: no evidence => structural? Spec: "原因合理但证据不充分：2；结构性损伤：0"
    # No evidence is "insufficient", not automatically structural, unless classified STRUCTURAL_DECLINE.
    if not adjusted and MispricingCategory.STRUCTURAL_DECLINE.value not in (cats or []) and impairment_state != "confirmed_impairment":
        rev_state = "plausible_insufficient" if False else "structural"
        # Conservative interpretation: without any evidence, do not award reversibility points.
        rev_state = "structural"

    # Implied vs evidence
    gap = reverse_dcf_gap
    if gap is not None and gap >= 0.05 and supports and not media_only:
        implied_state = "strong"
    elif gap is not None and gap >= 0.01 and (supports or pending):
        implied_state = "partial"
    elif gap is not None and gap >= 0.03:
        # Quantitative pessimism without evidence: partial at most, not strong.
        implied_state = "partial"
    else:
        implied_state = "none"

    return {
        "evidence_ids": [e.evidence_id for e in adjusted],
        "confirmed_ids": [e.evidence_id for e in confirmed],
        "categories": cats,
        "reversibility_state": rev_state,
        "implied_vs_evidence_state": implied_state,
        "records": [e.model_dump(mode="json") for e in adjusted],
        "media_only_confirmed": media_only,
    }
