from typing import Any


SHARED_REVIEWER_SCHEMA_VERSION = "1.0"
ISSUE_SEVERITY = {
    "unsupported_claim": "critical",
    "unsupported_inference": "major",
    "fabricated_figure": "critical",
    "fabricated_entity": "critical",
    "evidence_mismatch": "critical",
    "internal_inconsistency": "critical",
    "contradiction": "critical",
    "unmatched_evidence_used": "critical",
    "unsupported_motivation": "major",
    "requirement_omission": "major",
    "limit_violation": "critical",
    "jd_wording_repeated": "major",
    "ai_tone": "major",
    "declared_evidence_unused": "major",
    "style_only": "advisory",
}
SHARED_REVIEW_ISSUE_TYPES = set(ISSUE_SEVERITY)


def normalise_finding(issue: dict[str, Any]) -> dict[str, Any] | None:
    issue_type = str(issue.get("type") or "")
    if issue_type not in SHARED_REVIEW_ISSUE_TYPES:
        return None
    severity = ISSUE_SEVERITY[issue_type]
    return {
        "type": issue_type,
        "severity": severity,
        "description": str(issue.get("description") or "Review required.").strip(),
        "evidence": str(issue.get("evidence") or "").strip(),
        "location": str(issue.get("location") or "").strip(),
        "recommended_action": str(issue.get("recommended_action") or "Review or regenerate the affected content.").strip(),
        "blocks_release": severity in {"critical", "major"},
    }


def findings_block_release(findings: list[dict[str, Any]]) -> bool:
    return any(bool(item.get("blocks_release")) for item in findings)


def normalise_document_review(raw: dict[str, Any], document_id: str) -> dict[str, Any]:
    findings = []
    for issue in raw.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        finding = normalise_finding(issue)
        if finding:
            findings.append(finding)
    status = "fail" if findings_block_release(findings) else "pass"
    return {
        "schema_version": SHARED_REVIEWER_SCHEMA_VERSION,
        "status": status,
        "results": [{
            "criteria_id": document_id,
            "status": status,
            "issues": findings,
            "recommendation": str(raw.get("recommendation") or "").strip(),
        }],
    }
