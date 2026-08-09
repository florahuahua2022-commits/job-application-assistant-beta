import math
import re
from collections import Counter
from typing import Any


SELECTION_PLAN_SCHEMA_VERSION = "1.0"


def evidence_status(match_type: str, coverage: str) -> str:
    match_type = match_type.lower()
    coverage = coverage.lower()
    if match_type == "direct" and coverage == "strong":
        return "strong"
    if match_type == "direct" and coverage == "partial":
        return "transferable"
    if match_type == "inferred" and coverage in {"strong", "partial"}:
        return "transferable"
    return "weak"


def allocate_word_limits(job_model: dict[str, Any], default_target: int = 350) -> dict[str, int]:
    criteria = list(job_model.get("criteria") or [])
    if not criteria:
        return {}
    scope = job_model.get("limit_scope")
    if scope == "per_criteria" and job_model.get("per_criteria_word_limit"):
        value = int(job_model["per_criteria_word_limit"])
        return {str(item["criteria_id"]): value for item in criteria}
    if scope != "total" or not job_model.get("total_word_limit"):
        return {str(item["criteria_id"]): int(default_target) for item in criteria}

    total = int(job_model["total_word_limit"])
    weights: list[float] = []
    for item in criteria:
        weight = 1.15 if item.get("criteria_type") == "essential" else 1.0
        if len(item.get("criterion_categories") or []) >= 2:
            weight *= 1.10
        weights.append(weight)
    raw = [total * weight / sum(weights) for weight in weights]
    allocated = [math.floor(value) for value in raw]
    remaining = total - sum(allocated)
    order = sorted(range(len(raw)), key=lambda index: raw[index] - allocated[index], reverse=True)
    for index in order[:remaining]:
        allocated[index] += 1
    return {str(item["criteria_id"]): allocated[index] for index, item in enumerate(criteria)}


def _source_name(evidence: dict[str, Any]) -> str:
    section = str(evidence.get("source_section") or "Unknown source")
    parts = [part.strip() for part in section.split(">") if part.strip()]
    return parts[1] if len(parts) >= 3 else parts[0] if parts else "Unknown source"


def build_selection_plan(
    job_model: dict[str, Any],
    matches: dict[str, Any],
    ckb: list[dict[str, Any]],
    default_target: int = 350,
) -> dict[str, Any]:
    allocations = allocate_word_limits(job_model, default_target)
    criteria_by_id = {str(item.get("criteria_id")): item for item in job_model.get("criteria") or []}
    evidence_by_id = {str(item.get("evidence_id")): item for item in ckb}
    items = []
    primary_ids: list[str] = []
    primary_sources: list[str] = []
    for match in matches.get("matches") or []:
        criteria_id = str(match.get("criteria_id"))
        criterion = criteria_by_id.get(criteria_id)
        if not criterion:
            continue
        matched = [str(value) for value in match.get("matched_evidence") or [] if str(value) in evidence_by_id]
        if matched:
            primary_ids.append(matched[0])
            primary_sources.append(_source_name(evidence_by_id[matched[0]]))
        items.append({
            "criteria_id": criteria_id,
            "criteria_text": str(criterion.get("criteria_text") or ""),
            "criteria_type": str(criterion.get("criteria_type") or "inferred"),
            "allocated_word_limit": allocations.get(criteria_id, default_target),
            "matched_evidence": matched,
            "match_type": str(match.get("match_type") or "insufficient"),
            "coverage": str(match.get("coverage") or "weak"),
            "evidence_status": evidence_status(str(match.get("match_type") or "insufficient"), str(match.get("coverage") or "weak")),
        })
    reuse = Counter(primary_ids)
    sources = Counter(primary_sources)
    source_share = {source: count / len(primary_sources) for source, count in sources.items()} if primary_sources else {}
    warnings = []
    for evidence_id, count in reuse.items():
        if count > 2:
            warnings.append(f"Primary evidence {evidence_id} is used for {count} criteria; confirm that no comparably strong alternative exists.")
    for source, share in source_share.items():
        if share > 0.40 and len(primary_sources) >= 3:
            warnings.append(f"{source} supplies {round(share * 100)}% of primary evidence; keep this only when it is materially strongest.")
    return {
        "schema_version": SELECTION_PLAN_SCHEMA_VERSION,
        "limit_scope": str(job_model.get("limit_scope") or "unspecified"),
        "total_word_limit": job_model.get("total_word_limit"),
        "items": items,
        "primary_evidence_reuse": dict(reuse),
        "primary_source_share": source_share,
        "warnings": warnings,
    }


def actual_word_count(value: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", value, flags=re.UNICODE))


def hard_validate_response(
    response: dict[str, Any],
    plan_item: dict[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    final_response = str(response.get("final_response") or "")
    actual = actual_word_count(final_response)
    allocated = int(plan_item.get("allocated_word_limit") or 0)
    allowed_evidence = set(plan_item.get("matched_evidence") or [])
    evidence_used = {str(value) for value in response.get("evidence_used") or []}
    unknown = evidence_used - allowed_evidence
    if unknown:
        issues.append({"code": "unmatched_evidence_used", "message": "The response uses evidence that was not supplied by the Matcher."})
    if allocated and actual > math.floor(allocated * 1.05):
        issues.append({"code": "word_limit_exceeded", "message": f"The response contains {actual} words; the allowed tolerance is {math.floor(allocated * 1.05)}."})
    if not final_response.strip():
        issues.append({"code": "missing_final_response", "message": "The Generator returned no final response."})
    star = response.get("star")
    if not isinstance(star, dict) or not all(key in star for key in ("situation", "task", "action", "result")):
        issues.append({"code": "invalid_star_structure", "message": "The structured STAR audit fields are incomplete."})
    return {
        "valid": not issues,
        "actual_word_count": actual,
        "word_limit_exceeded": any(item["code"] == "word_limit_exceeded" for item in issues),
        "issues": issues,
    }
