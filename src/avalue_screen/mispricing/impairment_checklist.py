"""Permanent-impairment checklist. Each item is YES / NO / UNKNOWN with evidence."""

from __future__ import annotations

from typing import Any

from avalue_screen.domain.enums import ImpairmentAnswer, MispricingCategory
from avalue_screen.domain.models import CompanyInput, ImpairmentCheck

DEFAULT_QUESTIONS = [
    ("IMP-01", "核心产品或服务是否被技术替代？"),
    ("IMP-02", "核心客户是否永久流失或供应商地位下降？"),
    ("IMP-03", "市场份额、渠道或成本优势是否发生结构性恶化？"),
    ("IMP-04", "行业长期需求是否出现不可逆收缩？"),
    ("IMP-05", "ROIC是否连续多年低于资本成本且看不到修复路径？"),
    ("IMP-06", "资产负债表是否需要持续外部融资才能维持经营？"),
    ("IMP-07", "是否存在严重治理、关联交易或资本分配问题？"),
    ("IMP-08", "核心资产是否存在重大减值、抵押、冻结或产权争议？"),
]


def evaluate_impairment(company: CompanyInput) -> dict[str, Any]:
    provided = {c.item_id: c for c in company.impairment_checks}
    answers: list[dict[str, Any]] = []
    for item_id, question in DEFAULT_QUESTIONS:
        rec = provided.get(item_id)
        if rec is None:
            # Try match by order / question
            rec = next((c for c in company.impairment_checks if c.question == question), None)
        if rec is None:
            rec = ImpairmentCheck(item_id=item_id, question=question, answer=ImpairmentAnswer.UNKNOWN)
        answers.append(
            {
                "item_id": rec.item_id,
                "question": rec.question or question,
                "answer": rec.answer.value if hasattr(rec.answer, "value") else str(rec.answer),
                "evidence_summary": rec.evidence_summary,
                "evidence_refs": rec.evidence_refs,
                "material_to_long_term_earnings": rec.material_to_long_term_earnings,
            }
        )

    yes = [a for a in answers if a["answer"] == "YES"]
    unknown = [a for a in answers if a["answer"] == "UNKNOWN"]
    material_yes = [a for a in yes if a.get("material_to_long_term_earnings") is not False]

    if material_yes:
        state = "confirmed_impairment"
        category = MispricingCategory.STRUCTURAL_DECLINE.value
        review = True
    elif unknown or not company.impairment_checks:
        state = "uncertain"
        category = None
        review = False
    else:
        state = "no_impairment"
        category = None
        review = False

    return {
        "impairment_state": state,
        "impairment_answers": {a["item_id"]: a["answer"] for a in answers},
        "checks": answers,
        "structural_category": category,
        "review_required": review,
        "material_yes_items": [a["item_id"] for a in material_yes],
    }
