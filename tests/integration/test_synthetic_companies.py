"""13.2 Synthetic-company classification tests."""

from __future__ import annotations

from datetime import date

import pytest

from avalue_screen.config import load_config
from avalue_screen.domain.enums import EligibilityStatus, Grade, RunMode
from avalue_screen.ingestion.loaders import load_input
from avalue_screen.pipeline import screen_universe
from avalue_screen.screening.ranking import official_candidates
from tests.fixtures.generate_fixtures import AS_OF, write_universe


@pytest.fixture(scope="module")
def run(config, tmp_path_factory):
    dest = tmp_path_factory.mktemp("syn")
    write_universe(dest)
    payload = load_input(dest / "universe.json", config)
    return screen_universe(payload, AS_OF, RunMode.CURRENT_SCREEN, config)


def _by_id(run, sid: str):
    return next(r for r in run.results if r.security_id == sid)


def test_good_cheap_is_a_or_b_candidate(run):
    r = _by_id(run, "GOOD_CHEAP")
    assert r.eligibility_status is EligibilityStatus.PASS, (r.eligibility_status, r.rejection_reasons, r.review_flags, r.insufficient_reasons)
    assert r.scores["grade"] in {Grade.A.value, Grade.B.value}, r.scores
    assert r.candidate_eligible is True
    assert r.scores["commercial_quality"] >= 15
    assert r.scores["financial_health"] >= 12
    cands = {c.security_id for c in official_candidates(run.results)}
    assert "GOOD_CHEAP" in cands


def test_good_expensive_is_not_grade_a(run):
    r = _by_id(run, "GOOD_EXPENSIVE")
    assert r.scores["grade"] != Grade.A.value, r.scores
    mos = (r.valuation or {}).get("margin_of_safety")
    assert mos is None or mos < 0.30


def test_cheap_trap_is_review_or_reject(run):
    r = _by_id(run, "CHEAP_TRAP")
    assert r.eligibility_status in {EligibilityStatus.REVIEW_REQUIRED, EligibilityStatus.REJECT}
    ids = {h.rule_id for h in r.review_flags + r.rejection_reasons}
    # At least one of the quality-trap rules should fire
    assert ids & {"R-003", "R-006", "R-011", "R-002", "R-001"}
    cands = {c.security_id for c in official_candidates(run.results)}
    assert "CHEAP_TRAP" not in cands


def test_cyclical_trough_is_not_mechanically_rejected_on_current_pe(run):
    r = _by_id(run, "CYCLICAL_TROUGH")
    # Must not be hard-rejected solely because current (trough) PE looks expensive
    assert r.eligibility_status is not EligibilityStatus.REJECT
    assert (r.key_metrics or {}).get("normalized_method") in {"midcycle_5y_median", "midcycle_5y_median"} or "CYCLICAL" in str((r.valuation or {}).get("assumptions") or {}) or r.sector == "steel"
    # Mid-cycle method is recorded
    assert (r.key_metrics or {}).get("normalized_method") == "midcycle_5y_median"


def test_structural_decline_is_not_called_mispricing(run):
    r = _by_id(run, "STRUCTURAL_DECLINE")
    cats = r.mispricing.get("category") or []
    assert "STRUCTURAL_DECLINE" in cats
    assert r.mispricing.get("impairment_state") == "confirmed_impairment"
    # A large price drop must not produce a high mispricing / A grade
    assert r.scores.get("grade") != Grade.A.value
    assert r.eligibility_status in {EligibilityStatus.REVIEW_REQUIRED, EligibilityStatus.REJECT}
    e_div = None
    dims = r.scores.get("dimensions") or {}
    items = (dims.get("mispricing_evidence") or {}).get("items") or []
    div = next((i for i in items if i.get("item_id") == "E_divergence"), None)
    # Either missing comparison or a low divergence score (price drop with fundamental decline)
    if div and not div.get("missing") and div.get("score") is not None:
        assert div["score"] <= 1
    rev = next((i for i in items if i.get("item_id") == "E_reversibility"), None)
    if rev:
        assert rev.get("score") == 0
    cands = {c.security_id for c in official_candidates(run.results)}
    assert "STRUCTURAL_DECLINE" not in cands


def test_dilution_trap_is_penalized(run):
    r = _by_id(run, "DILUTION_TRAP")
    ids = {h.rule_id for h in r.review_flags}
    assert "R-010" in ids or r.eligibility_status is EligibilityStatus.REVIEW_REQUIRED
    dims = r.scores.get("dimensions") or {}
    items = (dims.get("earnings_quality") or {}).get("items") or []
    eps_item = next((i for i in items if i.get("item_id") == "C_eps_shares"), None)
    assert eps_item is not None
    if not eps_item.get("missing"):
        assert eps_item.get("score") == 0


def test_missing_data_is_insufficient_not_zero_filled(run):
    r = _by_id(run, "MISSING_DATA")
    assert r.eligibility_status is EligibilityStatus.INSUFFICIENT_DATA
    assert r.scores.get("grade") == Grade.INSUFFICIENT_DATA.value
    # Official candidate list must not include it
    cands = {c.security_id for c in official_candidates(run.results)}
    assert "MISSING_DATA" not in cands
    # Score items that lack inputs must be missing, not silently 0-ranked as official
    assert r.insufficient_reasons


def test_negative_fcf_growth_is_review_not_fraud_reject(run):
    r = _by_id(run, "NEGATIVE_FCF_GROWTH")
    assert r.eligibility_status is EligibilityStatus.REVIEW_REQUIRED
    ids = {h.rule_id for h in r.review_flags}
    assert "R-012" in ids or "R-001" in ids
    assert r.eligibility_status is not EligibilityStatus.REJECT
    # Not treated as accounting fraud
    assert not any(h.rule_id in {"G-002", "G-003"} for h in r.rejection_reasons)


def test_review_and_insufficient_are_not_on_candidate_list(run):
    cands = official_candidates(run.results)
    for c in cands:
        assert c.eligibility_status is EligibilityStatus.PASS
        assert c.scores.get("grade") not in {"REVIEW", "INSUFFICIENT_DATA"}
