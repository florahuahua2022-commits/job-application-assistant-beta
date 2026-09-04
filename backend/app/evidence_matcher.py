import json
from typing import Any


MATCH_SCHEMA_VERSION = "1.0"


def normalise_match_result(raw: dict[str, Any], job_model: dict[str, Any], ckb: list[dict[str, Any]]) -> dict[str, Any]:
    valid_evidence = {str(item.get("evidence_id")) for item in ckb if item.get("evidence_id")}
    criteria = {str(item.get("criteria_id")): item for item in job_model.get("criteria") or []}
    normalised: list[dict[str, Any]] = []
    seen_criteria: set[str] = set()
    for item in raw.get("matches") or []:
        if not isinstance(item, dict):
            continue
        criteria_id = str(item.get("criteria_id") or "")
        if criteria_id not in criteria or criteria_id in seen_criteria:
            continue
        evidence_ids = []
        for evidence_id in item.get("matched_evidence") or []:
            value = str(evidence_id)
            if value in valid_evidence and value not in evidence_ids:
                evidence_ids.append(value)
        match_type = str(item.get("match_type") or "insufficient").lower()
        coverage = str(item.get("coverage") or "weak").lower()
        if match_type not in {"direct", "inferred", "insufficient"}:
            match_type = "insufficient"
        if coverage not in {"strong", "partial", "weak"}:
            coverage = "weak"
        if not evidence_ids:
            match_type, coverage = "insufficient", "weak"
        normalised.append({
            "criteria_id": criteria_id,
            "matched_evidence": evidence_ids,
            "match_type": match_type,
            "coverage": coverage,
            "reasoning": str(item.get("reasoning") or "No matching explanation was returned.").strip(),
        })
        seen_criteria.add(criteria_id)
    for criteria_id in criteria:
        if criteria_id not in seen_criteria:
            normalised.append({
                "criteria_id": criteria_id,
                "matched_evidence": [],
                "match_type": "insufficient",
                "coverage": "weak",
                "reasoning": "No supportable evidence was matched.",
            })
    used = {evidence_id for item in normalised for evidence_id in item["matched_evidence"]}
    return {
        "schema_version": MATCH_SCHEMA_VERSION,
        "matches": normalised,
        "unused_evidence": sorted(valid_evidence - used),
    }


def validate_match_result(result: dict[str, Any], job_model: dict[str, Any], ckb: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    valid_evidence = {str(item.get("evidence_id")) for item in ckb}
    valid_criteria = {str(item.get("criteria_id")) for item in job_model.get("criteria") or []}
    returned_criteria: set[str] = set()
    for index, item in enumerate(result.get("matches") or [], start=1):
        criteria_id = str(item.get("criteria_id") or "")
        if criteria_id not in valid_criteria:
            errors.append(f"Match {index} references an unknown criterion.")
        returned_criteria.add(criteria_id)
        if item.get("match_type") not in {"direct", "inferred", "insufficient"}:
            errors.append(f"Match {index} has an invalid match_type.")
        if item.get("coverage") not in {"strong", "partial", "weak"}:
            errors.append(f"Match {index} has invalid coverage.")
        unknown = set(item.get("matched_evidence") or []) - valid_evidence
        if unknown:
            errors.append(f"Match {index} references unknown evidence.")
    if returned_criteria != valid_criteria:
        errors.append("The matcher did not return exactly one result for every criterion.")
    return errors


def matched_evidence_pack(ckb_json: str, matches_json: str, max_items: int = 12) -> list[dict[str, str]]:
    try:
        ckb = json.loads(ckb_json or "[]")
        matches = json.loads(matches_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return []
    by_id = {str(item.get("evidence_id")): item for item in ckb if isinstance(item, dict)}
    ordered_ids: list[str] = []
    for match in matches.get("matches") or []:
        for evidence_id in match.get("matched_evidence") or []:
            value = str(evidence_id)
            if value in by_id and value not in ordered_ids:
                ordered_ids.append(value)
    return [{
        "evidence_id": evidence_id,
        "evidence_type": str(by_id[evidence_id].get("evidence_type") or "experience"),
        "source_section": str(by_id[evidence_id].get("source_section") or "Master Resume"),
        "source_text": str(by_id[evidence_id].get("source_text") or ""),
    } for evidence_id in ordered_ids[:max_items]]
