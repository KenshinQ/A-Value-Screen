"""Build explainability blocks: drivers, missing data, formula versions, sources."""

from __future__ import annotations

from typing import Any

from avalue_screen.config import ScreenConfig
from avalue_screen.domain.models import CompanyResult, ValuationResult
from avalue_screen.features.engine import FeatureSet
from avalue_screen.quality.data_quality import DataQualityReport


def collect_drivers(scores: dict[str, Any]) -> tuple[list[str], list[str]]:
    pos: list[str] = []
    neg: list[str] = []
    dims = scores.get("dimensions") or {}
    for dim_name, dim in dims.items():
        for item in dim.get("items") or []:
            sc = item.get("score")
            mx = item.get("max_score") or 0
            rationale = item.get("rationale") or ""
            if item.get("missing"):
                continue
            if sc is None:
                continue
            label = f"{item.get('item_id')}: {rationale}"
            if mx and sc >= 0.75 * mx:
                pos.append(label)
            elif sc <= 0.25 * mx:
                neg.append(label)
    return pos, neg


def build_explainability(
    features: FeatureSet,
    scores: dict[str, Any],
    dq: DataQualityReport,
    valuation: ValuationResult | None,
    config: ScreenConfig,
    extra_missing: list[str] | None = None,
) -> dict[str, Any]:
    pos, neg = collect_drivers(scores)
    missing = list(dq.missing_fields)
    if extra_missing:
        missing.extend(extra_missing)
    dims = scores.get("dimensions") or {}
    for dim in dims.values():
        for item in dim.get("items") or []:
            if item.get("missing"):
                missing.append(item.get("item_id"))
    formula_versions = config.get("formula_versions") or {}
    sources: list[str] = []
    if features.annual:
        sources.append(f"{len(features.annual)} annual periods; TTM as of {features.ttm_period}")
    return {
        "positive_drivers": pos,
        "negative_drivers": neg,
        "missing_data": sorted(set(m for m in missing if m)),
        "formula_versions": formula_versions,
        "source_refs": sources,
        "data_quality_reasons": dq.reasons,
        "feature_flags": features.flags,
        "valuation_flags": valuation.flags if valuation else [],
        "valuation_assumptions": valuation.assumptions if valuation else {},
        "valuation_confidence": valuation.confidence if valuation else None,
    }
