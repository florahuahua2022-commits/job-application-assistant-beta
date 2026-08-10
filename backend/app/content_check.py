import re
from typing import Any


SEVERITY_ORDER = {"information": 0, "warning": 1, "error": 2}


def _normalise_severity(value: str) -> str:
    lowered = (value or "").strip().lower()
    if lowered in {"critical", "major", "error", "blocking"}:
        return "error"
    if lowered in {"advisory", "warning"}:
        return "warning"
    return "information"


def _root_code(issue: dict[str, Any], evaluative_documents: set[str]) -> str:
    code = str(issue.get("code") or "content_check_finding")
    document_type = str(issue.get("document_type") or "")
    aliases = {
        "reviewer_ai_tone": "unsupported_evaluative_claim",
        "reviewer_jd_wording_repeated": "jd_wording_repeated",
        "reviewer_unsupported_motivation": "unsupported_motivation",
    }
    code = aliases.get(code, code)
    if code == "generic_ai_wording" and document_type in evaluative_documents:
        return "unsupported_evaluative_claim"
    return code


def _dedupe_key(issue: dict[str, Any], root_code: str) -> tuple[str, str, str]:
    document_type = str(issue.get("document_type") or "")
    if root_code in {"unsupported_evaluative_claim", "jd_wording_repeated"}:
        return document_type, root_code, ""
    anchor = str(issue.get("excerpt") or issue.get("location") or issue.get("message") or "")
    anchor = " ".join(re.findall(r"[a-z0-9]+", anchor.lower()))[:240]
    return document_type, root_code, anchor


def consolidate_quality_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise severity and merge only findings that share a known root cause."""
    evaluative_documents = {
        str(issue.get("document_type") or "")
        for issue in issues
        if str(issue.get("code") or "") in {"unsupported_evaluative_claim", "reviewer_ai_tone"}
    }
    consolidated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in issues:
        issue = dict(raw)
        issue["severity"] = _normalise_severity(str(issue.get("severity") or ""))
        root_code = _root_code(issue, evaluative_documents)
        issue["code"] = root_code
        key = _dedupe_key(issue, root_code)
        existing = consolidated.get(key)
        if existing is None:
            consolidated[key] = issue
            continue
        if SEVERITY_ORDER[issue["severity"]] > SEVERITY_ORDER[existing["severity"]]:
            existing["severity"] = issue["severity"]
        if root_code == "unsupported_evaluative_claim":
            existing["message"] = "The document uses unsupported evaluative wording (for example, 'proven capability' or 'strong record'). Replace it with plain, evidence-based facts."
            existing["rule"] = "Claims and performance evaluations must be directly supported by applicant evidence."
            existing["recommended_action"] = "Remove the evaluation or replace it with the specific supported duty, result or measure."
        for field in ("excerpt", "source", "rule", "recommended_action"):
            if not existing.get(field) and issue.get(field):
                existing[field] = issue[field]

    return sorted(
        consolidated.values(),
        key=lambda item: (
            -SEVERITY_ORDER[str(item.get("severity") or "information")],
            str(item.get("document_type") or ""),
            str(item.get("code") or ""),
        ),
    )
