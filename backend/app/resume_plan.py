from collections import Counter
import json
import re
from typing import Any


RESUME_PLAN_SCHEMA_VERSION = "1.0"


def validate_resume_content(content: str, plan: dict[str, Any], evidence_used: list[str]) -> dict[str, Any]:
    issues = []
    for section in plan.get("required_sections") or []:
        if not re.search(rf"(?im)^#+\s*{re.escape(str(section))}\s*$", content):
            issues.append({"code": "missing_required_section", "message": f"The CV is missing the {section} section."})
    word_count = len(re.findall(r"\b[\w'-]+\b", content, flags=re.UNICODE))
    if word_count > 750:
        issues.append({"code": "resume_too_long", "message": f"The CV contains {word_count} words; the two-page target allows at most 750."})
    unknown = set(map(str, evidence_used)) - selected_resume_evidence_ids(plan)
    if unknown:
        issues.append({"code": "unselected_evidence_used", "message": "The CV uses evidence outside the Resume Curation Plan."})
    return {"valid": not issues, "word_count": word_count, "issues": issues}


def selected_resume_evidence_ids(plan: dict[str, Any]) -> set[str]:
    return {
        str(item.get("evidence_id"))
        for item in plan.get("selected_evidence") or []
        if item.get("evidence_id")
    }


def resume_evidence_pack(ckb_json: str, plan_json: str) -> list[dict[str, Any]]:
    try:
        ckb = json.loads(ckb_json or "[]")
        plan = json.loads(plan_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return []
    by_id = {str(item.get("evidence_id")): item for item in ckb if isinstance(item, dict)}
    ordered_ids = [str(item.get("evidence_id")) for item in plan.get("selected_evidence") or [] if item.get("evidence_id")]
    return [by_id[evidence_id] for evidence_id in ordered_ids if evidence_id in by_id]


def build_resume_curation_plan(
    job_model: dict[str, Any],
    matches: dict[str, Any],
    ckb: list[dict[str, Any]],
    target_words: int = 650,
    max_evidence: int = 10,
) -> dict[str, Any]:
    evidence_by_id = {str(item.get("evidence_id")): item for item in ckb if item.get("evidence_id")}
    relevance = Counter()
    priority_requirements: dict[str, list[str]] = {}
    criteria_by_id = {str(item.get("criteria_id")): item for item in job_model.get("criteria") or []}
    for match in matches.get("matches") or []:
        criterion = criteria_by_id.get(str(match.get("criteria_id")), {})
        base = {"direct": 6, "inferred": 3, "insufficient": 0}.get(str(match.get("match_type")), 0)
        base += {"strong": 3, "partial": 1, "weak": 0}.get(str(match.get("coverage")), 0)
        if criterion.get("criteria_type") == "essential":
            base += 2
        for rank, evidence_id in enumerate(match.get("matched_evidence") or []):
            value = str(evidence_id)
            if value not in evidence_by_id:
                continue
            relevance[value] += max(base - rank, 1)
            priority_requirements.setdefault(value, []).append(str(criterion.get("criteria_id") or ""))

    quality_score = {"high": 3, "medium": 2, "low": 1}
    ranked = sorted(
        evidence_by_id.values(),
        key=lambda item: (
            relevance[str(item.get("evidence_id"))],
            quality_score.get(str(item.get("evidence_quality")), 0),
            bool(str(item.get("result") or "").strip()),
        ),
        reverse=True,
    )
    selected = []
    for item in ranked:
        evidence_id = str(item.get("evidence_id"))
        if not relevance[evidence_id] and item.get("evidence_type") not in {"education", "qualification", "award"}:
            continue
        selected.append({
            "evidence_id": evidence_id,
            "evidence_type": str(item.get("evidence_type") or "experience"),
            "source_section": str(item.get("source_section") or "Master Resume"),
            "supports_requirements": sorted(set(priority_requirements.get(evidence_id, []))),
            "curation_action": "feature" if relevance[evidence_id] >= 8 else "include_concisely",
            "fact_policy": "preserve_source_facts_only",
        })
        if len(selected) >= max_evidence:
            break
    return {
        "schema_version": RESUME_PLAN_SCHEMA_VERSION,
        "target_words": target_words,
        "maximum_pages": 2,
        "required_sections": ["Professional Summary", "Key Skills", "Work Experience"],
        "selected_evidence": selected,
        "omitted_evidence_ids": sorted(set(evidence_by_id) - {item["evidence_id"] for item in selected}),
        "section_budget": {
            "professional_summary": 80,
            "key_skills": 90,
            "work_experience": max(target_words - 220, 250),
            "education_qualifications_references": 50,
        },
        "rules": [
            "Reorder and compress evidence for relevance without changing facts.",
            "Do not create achievements, metrics, titles, employers or dates.",
            "Keep selected evidence editable and traceable to CKB source_text.",
        ],
    }
