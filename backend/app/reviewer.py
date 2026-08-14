from typing import Any
from .reviewer_core import SHARED_REVIEW_ISSUE_TYPES, findings_block_release, normalise_finding


REVIEW_SCHEMA_VERSION = "1.0"
SELECTION_REVIEW_ISSUE_TYPES = {
    "unsupported_claim", "unsupported_inference", "fabricated_figure", "fabricated_entity", "evidence_mismatch",
    "internal_inconsistency", "jd_wording_repeated", "ai_tone", "declared_evidence_unused",
    "unmatched_evidence_used",
}
REVIEW_ISSUE_TYPES = SELECTION_REVIEW_ISSUE_TYPES


def normalise_review_result(raw: dict[str, Any], criteria_ids: list[str]) -> dict[str, Any]:
    allowed = set(criteria_ids)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw.get("results") or raw.get("reviews") or []:
        if not isinstance(item, dict):
            continue
        criteria_id = str(item.get("criteria_id") or "")
        if criteria_id not in allowed or criteria_id in seen:
            continue
        issues = []
        for issue in item.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            issue_type = str(issue.get("type") or "")
            if issue_type not in SELECTION_REVIEW_ISSUE_TYPES:
                continue
            finding = normalise_finding(issue)
            if finding:
                issues.append(finding)
        status = "fail" if findings_block_release(issues) or str(item.get("status") or "").lower() == "fail" else "pass"
        results.append({
            "criteria_id": criteria_id,
            "status": status,
            "issues": issues,
            "recommendation": str(item.get("recommendation") or "").strip(),
        })
        seen.add(criteria_id)
    for criteria_id in criteria_ids:
        if criteria_id not in seen:
            results.append({
                "criteria_id": criteria_id,
                "status": "fail",
                "issues": [normalise_finding({"type": "unsupported_claim", "description": "The Reviewer returned no decision for this criterion."})],
                "recommendation": "Regenerate or review this criterion manually.",
            })
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "status": "fail" if any(item["status"] == "fail" for item in results) else "pass",
        "results": results,
    }


def validate_review_result(result: dict[str, Any], criteria_ids: list[str]) -> list[str]:
    errors: list[str] = []
    returned = [str(item.get("criteria_id")) for item in result.get("results") or []]
    if sorted(returned) != sorted(criteria_ids):
        errors.append("Reviewer must return exactly one decision for every criterion.")
    for index, item in enumerate(result.get("results") or [], start=1):
        if item.get("status") not in {"pass", "fail"}:
            errors.append(f"Review {index} has an invalid status.")
        for issue in item.get("issues") or []:
            if issue.get("type") not in SHARED_REVIEW_ISSUE_TYPES:
                errors.append(f"Review {index} contains an unsupported issue type.")
    return errors
