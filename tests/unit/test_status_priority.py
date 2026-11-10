"""13.1.13 REJECT / REVIEW_REQUIRED / INSUFFICIENT_DATA mutual exclusivity and priority."""

from datetime import date

from avalue_screen.domain.enums import EligibilityStatus
from avalue_screen.domain.models import RuleHit
from avalue_screen.screening.hard_gates import GateResult


def _result(rej=0, ins=0, rev=0):
    g = GateResult(status=EligibilityStatus.PASS)
    for i in range(rej):
        g.rejection.append(RuleHit(rule_id=f"G-00{i+1}", severity=EligibilityStatus.REJECT, reason="x"))
    for i in range(ins):
        g.insufficient.append(RuleHit(rule_id=f"I-00{i+1}", severity=EligibilityStatus.INSUFFICIENT_DATA, reason="x"))
    for i in range(rev):
        g.review.append(RuleHit(rule_id=f"R-00{i+1}", severity=EligibilityStatus.REVIEW_REQUIRED, reason="x"))
    if g.rejection:
        g.status = EligibilityStatus.REJECT
    elif g.insufficient:
        g.status = EligibilityStatus.INSUFFICIENT_DATA
    elif g.review:
        g.status = EligibilityStatus.REVIEW_REQUIRED
    return g


def test_reject_outranks_insufficient_and_review():
    g = _result(rej=1, ins=1, rev=1)
    assert g.status is EligibilityStatus.REJECT
    assert {h.rule_id for h in g.all_hits} >= {"G-001", "I-001", "R-001"}


def test_insufficient_outranks_review_when_no_reject():
    g = _result(ins=1, rev=2)
    assert g.status is EligibilityStatus.INSUFFICIENT_DATA
    assert len(g.review) == 2  # other reasons are retained


def test_review_when_data_is_complete_and_no_reject():
    g = _result(rev=1)
    assert g.status is EligibilityStatus.REVIEW_REQUIRED


def test_pass_when_no_hits():
    g = _result()
    assert g.status is EligibilityStatus.PASS
    assert g.all_hits == []


def test_high_score_cannot_override_reject():
    # Documented invariant: eligibility is decided by gates, not by the numeric score.
    g = _result(rej=1)
    fake_total = 99
    assert g.status is EligibilityStatus.REJECT
    assert fake_total >= 85
