from typing import Any


SHARED_REVIEWER_SCHEMA_VERSION = "1.0"
ISSUE_SEVERITY = {
    "unsupported_claim": "critical",
    "unsupported_inference": "major",
    "fabricated_figure": "critical",
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
