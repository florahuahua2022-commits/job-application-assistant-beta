from difflib import SequenceMatcher
import re
from typing import Any


SHARED_REVIEWER_SCHEMA_VERSION = "1.0"
REVIEW_POLICY_VERSION = "2.0"
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
    "unknown_reviewer_issue": "major",
    "style_only": "advisory",
    "unsupported_availability_claim": "major",
    "availability_conflict": "major",
    "generation_under_utilized": "major",
    "insufficient_source_detail": "major",
    "concise_but_relevant": "advisory",
    "source_parsing_uncertain": "advisory",
    "resume_repetitive_opening": "advisory",
    "thin_evidence_repeated": "major",
    "missing_role_header": "critical",
    "role_order_mismatch": "critical",
    "omitted_role_expanded": "critical",
}
SHARED_REVIEW_ISSUE_TYPES = set(ISSUE_SEVERITY)


def normalise_finding(issue: dict[str, Any]) -> dict[str, Any] | None:
    reported_type = str(issue.get("type") or "missing_type")
    issue_type = reported_type if reported_type in SHARED_REVIEW_ISSUE_TYPES else "unknown_reviewer_issue"
    severity = ISSUE_SEVERITY[issue_type]
    description = str(issue.get("description") or "Review required.").strip()
    recommended_action = str(issue.get("recommended_action") or "Review or regenerate the affected content.").strip()
    action = recommended_action.casefold().strip(" .")
    if (
        recommended_action.casefold().startswith(("no change required", "no action required"))
        or action in {"none", "advisory only", "no change", "no action"}
        or re.search(r"\bno (?:actual )?defect\.?$", description.casefold())
    ):
        return None
    if issue_type == "unknown_reviewer_issue":
        description = f"Reviewer returned unsupported issue type '{reported_type}': {description}"
    finding = {
        "type": issue_type,
        "severity": severity,
        "description": description,
        "evidence": str(issue.get("evidence") or "").strip(),
        "location": str(issue.get("location") or "").strip(),
        "recommended_action": recommended_action,
        "blocks_release": severity in {"critical", "major"},
    }
    if issue.get("location_kind") in {"exact_quote", "section", "document_wide"}:
        finding["location_kind"] = issue["location_kind"]
    return finding


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
        "review_policy_version": REVIEW_POLICY_VERSION,
        "status": status,
        "results": [{
            "criteria_id": document_id,
            "status": status,
            "issues": findings,
            "recommendation": str(raw.get("recommendation") or "").strip(),
        }],
    }


def _normalised_text(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()


def _phrase_in_content(phrase: str, content: str) -> bool:
    phrase_text = _normalised_text(phrase)
    content_text = _normalised_text(content)
    if not phrase_text or phrase_text in content_text:
        return bool(phrase_text)
    phrase_words = phrase_text.split()
    content_words = content_text.split()
    if len(phrase_words) < 4 or len(content_words) < len(phrase_words):
        return False
    size = len(phrase_words)
    return any(
        SequenceMatcher(None, phrase_text, " ".join(content_words[index:index + size])).ratio() >= .9
        for index in range(len(content_words) - size + 1)
    )


def _description_document_quotes(description: str) -> list[str]:
    quotes = re.finditer(r"['\u2018\u2019\"]([^'\u2018\u2019\"]{4,})['\u2018\u2019\"]", description)
    document_terms = ("cv", "cover letter", "letter", "document", "response", "summary", "heading", "bullet", "body", "states", "claims", "reads", "lists", "uses", "describes")
    source_terms = ("ckb", "source_text", "source text", "evidence", "job description", " jd ", "resume plan", "applicant profile")
    result = []
    for match in quotes:
        context = f" {description[max(0, match.start() - 120):match.start()].casefold()} "
        document_position = max((context.rfind(term) for term in document_terms), default=-1)
        source_position = max((context.rfind(term) for term in source_terms), default=-1)
        document_claims = list(re.finditer(
            r"\b(?:letter|cv|document|response)\s+(?:uses|includes|contains|states|claims)\b(?:.{0,80}\bevidence\b)?",
            context,
        ))
        if document_claims:
            document_position = max(document_position, document_claims[-1].end())
        if document_position > source_position:
            result.append(match.group(1))
    return result


def reconcile_review_grounding(review: dict[str, Any], content: str | dict[str, str]) -> dict[str, Any]:
    """Prevent unverifiable AI quotations from blocking the reviewed content."""
    for result in review.get("results") or []:
        reviewed_content = content.get(str(result.get("criteria_id") or ""), "") if isinstance(content, dict) else content
        for issue in result.get("issues") or []:
            kind = issue.get("location_kind")
            phrases = [issue.get("location", "")] if kind == "exact_quote" else []
            phrases.extend(_description_document_quotes(str(issue.get("description") or "")))
            phrases = [str(phrase).strip() for phrase in phrases if str(phrase).strip()]
            if not phrases:
                continue
            if all(_phrase_in_content(phrase, reviewed_content) for phrase in phrases):
                issue["grounding_status"] = "verified"
                continue
            issue.update(
                severity="advisory",
                blocks_release=False,
                grounding_status="unverified",
                grounding_reason="quoted_location_not_found",
            )
        result["status"] = "fail" if findings_block_release(result.get("issues") or []) else "pass"
    review["status"] = "fail" if any(item.get("status") == "fail" for item in review.get("results") or []) else "pass"
    review["review_policy_version"] = REVIEW_POLICY_VERSION
    return review
