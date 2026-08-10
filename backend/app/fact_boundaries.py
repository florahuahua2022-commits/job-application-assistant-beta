import re
from typing import Any


def _normalise(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _contains(value: str, phrase: str) -> bool:
    return _normalise(phrase) in _normalise(value)


UNSUPPORTED_EVALUATIONS = (
    "proven capability",
    "strong record",
    "proven track record",
)

RESPONSIBILITY_OR_RESULT_UPGRADES = (
    "adapting to changing requirements",
    "adapted to changing requirements",
    "keeping deliverables on track",
    "kept deliverables on track",
    "take responsibility for delivering",
    "took responsibility for delivering",
    "accurate timely administrative outputs",
    "accurate and timely administrative outputs",
)

UNSUPPORTED_MOTIVATION_PATTERNS = (
    r"\bwhat draws me to\b",
    r"\bi am drawn to\b",
    r"\bi am motivated by\b",
    r"\bi am excited (?:by|about|to)\b",
    r"\bthis (?:role|position|opportunity) appeals to me\b",
    r"\bwhat attracted me to\b",
)

BROAD_ALIGNMENT_PATTERNS = (
    r"\baligns closely with (?:the )?work i have delivered throughout my career\b",
    r"\baligns closely with my experience throughout my career\b",
)


def find_fact_boundary_issues(
    content: str,
    evidence_text: str,
    *,
    motivation_confirmed: bool = False,
) -> list[dict[str, Any]]:
    """Find high-confidence claim-boundary violations without semantic guesswork."""
    issues: list[dict[str, Any]] = []
    for phrase in UNSUPPORTED_EVALUATIONS:
        if _contains(content, phrase) and not _contains(evidence_text, phrase):
            issues.append({
                "severity": "error",
                "code": "unsupported_evaluative_claim",
                "phrase": phrase,
                "message": f"The phrase '{phrase}' makes an evaluative claim that is not present in the applicant evidence.",
            })
    for phrase in RESPONSIBILITY_OR_RESULT_UPGRADES:
        if _contains(content, phrase) and not _contains(evidence_text, phrase):
            issues.append({
                "severity": "error",
                "code": "responsibility_or_result_upgrade",
                "phrase": phrase,
                "message": f"The phrase '{phrase}' adds a responsibility, performance quality or result that is not present in the applicant evidence.",
            })
    if not motivation_confirmed:
        for pattern in UNSUPPORTED_MOTIVATION_PATTERNS:
            match = re.search(pattern, content, flags=re.IGNORECASE)
            if match:
                issues.append({
                    "severity": "error",
                    "code": "unsupported_motivation",
                    "phrase": match.group(0),
                    "message": f"The phrase '{match.group(0)}' states personal motivation that the applicant has not confirmed.",
                })
    for pattern in BROAD_ALIGNMENT_PATTERNS:
        match = re.search(pattern, content, flags=re.IGNORECASE)
        if match:
            issues.append({
                "severity": "warning",
                "code": "broad_alignment_claim",
                "phrase": match.group(0),
                "message": "This is a broad interpretation of career alignment. Replace it with a specific, evidence-linked summary.",
            })
    return issues
