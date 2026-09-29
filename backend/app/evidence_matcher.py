import json
import hashlib
import re
import unicodedata
from typing import Any
from .job_model import match_advertised_tags


MATCH_SCHEMA_VERSION = "2.0"
MATCHER_RULES_VERSION = "exhaustive-matrix-v1"

_ATOM_PATTERNS = {
    "organisation": (r"\borganis(?:e|ed|es|ing|ation|ational)\b", r"\borganiz(?:e|ed|es|ing|ation|ational)\b", r"\bcoordinat(?:e|ed|es|ing|ion)\b", r"\bschedul(?:e|ed|es|ing)\b", r"\bplann(?:ed|ing)\b", r"\bprioriti[sz](?:e|ed|es|ing)\b", r"\btrack(?:ed|ing)?\b", r"\bmonitor(?:ed|ing)?\b"),
    "time_management": (r"\btime management\b", r"\bschedul(?:e|ed|es|ing)\b", r"\bpriorit(?:y|ies)\b", r"\bprioriti[sz](?:e|ed|es|ing)\b", r"\bdeadline(?:s)?\b", r"\btimeline(?:s)?\b", r"\bconcurrently\b", r"\bcompeting (?:priorities|tasks)\b"),
    "multitask": (r"\bmulti[- ]?task(?:ing)?\b", r"\bcompeting (?:priorities|tasks)\b", r"\bconcurrently\b", r"\bsimultaneously\b", r"\bmultiple (?:projects|tasks)\b"),
    "communication": (r"\bcommunicat(?:e|ed|es|ing|ion)\b", r"\bliais(?:e|ed|es|ing|on)\b", r"\bcorrespondence\b", r"\bstakeholder engagement\b"),
    "collaboration": (r"\bcollaborat(?:e|ed|es|ing|ion)\b", r"\bstakeholders?\b", r"\bmultidisciplinary\b", r"\bcross[- ]functional\b", r"\bteams?\b"),
    "diverse_backgrounds": (r"\bdiverse backgrounds\b", r"\bmulticultural\b", r"\binternational\b", r"\bcross[- ]agency\b"),
}
_NAMED_TOOL_TERMS = {"excel", "outlook", "word", "teams", "sap", "sharepoint", "procore", "aconex", "autodesk"}
_ROLE_TERM_STOP = {
    "ability", "adequate", "background", "beneficial", "excellent", "experience", "field", "from", "has",
    "ideally", "including", "knowledge", "preferred", "proficient", "skills", "strong", "studies", "successfully",
    "the", "with", "would", "and", "or", "in", "of", "to", "a", "an", "be",
}


def _normalise_text(value: Any) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", value).strip()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def match_cache_is_current(result: dict[str, Any], job_model: dict[str, Any], ckb: list[dict[str, Any]]) -> bool:
    return bool(
        result.get("schema_version") == MATCH_SCHEMA_VERSION
        and result.get("matcher_rules_version") == MATCHER_RULES_VERSION
        and result.get("job_model_fingerprint") == _fingerprint(job_model)
        and result.get("ckb_fingerprint") == _fingerprint(ckb)
    )


def _requirement_atoms(value: str) -> list[str]:
    text = _normalise_text(value)
    atoms = []
    if re.search(r"\borganis|\borganiz", text):
        atoms.append("organisation")
    if "time-management" in text or "time management" in text:
        atoms.append("time_management")
    if re.search(r"\bmulti[- ]?task", text):
        atoms.append("multitask")
    if "communication" in text:
        atoms.append("communication")
    if "collaboration" in text:
        atoms.append("collaboration")
    if "diverse backgrounds" in text:
        atoms.append("diverse_backgrounds")
    atoms.extend(f"tool:{tool}" for tool in sorted(_NAMED_TOOL_TERMS) if re.search(rf"\b{re.escape(tool)}\b", text))
    if not atoms:
        terms = []
        for token in re.findall(r"[a-z][a-z-]+", text):
            token = "admin" if token in {"admin", "administration", "administrative"} else token
            if len(token) > 3 and token not in _ROLE_TERM_STOP and token not in terms:
                terms.append(token)
        atoms.extend(f"term:{term}" for term in terms)
    return atoms


def _criterion_atoms(criterion: dict[str, Any]) -> list[str]:
    if "qualification" in (criterion.get("criterion_categories") or []):
        return []
    return _requirement_atoms(str(criterion.get("criteria_text") or ""))


def _patterns_for_atom(atom: str) -> tuple[str, ...]:
    if atom in _ATOM_PATTERNS:
        return _ATOM_PATTERNS[atom]
    if atom.startswith("tool:"):
        return (rf"\b{re.escape(atom[5:])}\b",)
    term = atom[5:]
    if term == "admin":
        return (r"\badmin(?:istration|istrative)?\b",)
    if term == "warehouse":
        return (r"\bwarehous(?:e|ing)\b",)
    if term == "reporting":
        return (r"\breport(?:s|ed|ing)?\b",)
    return (rf"\b{re.escape(term)}(?:s)?\b",)


def _ground_support(criterion: dict[str, Any], raw_support: list[dict[str, Any]], evidence_by_id: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    atoms = _criterion_atoms(criterion)
    if not atoms:
        return [], []
    grounded = []
    supported_union: set[str] = set()
    for item in raw_support:
        evidence_id = str(item.get("evidence_id") or "")
        quote = str(item.get("support_quote") or "").strip()
        source = str((evidence_by_id.get(evidence_id) or {}).get("source_text") or "")
        quote_text = _normalise_text(quote)
        if not quote_text or quote_text not in _normalise_text(source):
            supported = []
        else:
            supported = [atom for atom in atoms if any(re.search(pattern, quote_text) for pattern in _patterns_for_atom(atom))]
        supported_union.update(supported)
        grounded.append({
            "evidence_id": evidence_id,
            "support_quote": quote,
            "matched_requirement_terms": [str(value) for value in item.get("matched_requirement_terms") or []],
            "supported_atoms": supported,
            "unsupported_atoms": [atom for atom in atoms if atom not in supported],
        })
    return grounded, [atom for atom in atoms if atom in supported_union]


def normalise_match_result(raw: dict[str, Any], job_model: dict[str, Any], ckb: list[dict[str, Any]]) -> dict[str, Any]:
    valid_evidence = {str(item.get("evidence_id")) for item in ckb if item.get("evidence_id")}
    evidence_by_id = {str(item.get("evidence_id")): item for item in ckb if item.get("evidence_id")}
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
        raw_support = [item for item in item.get("evidence_support") or [] if str(item.get("evidence_id") or "") in evidence_ids]
        grounded_support, supported_atoms = _ground_support(
            criteria[criteria_id], raw_support, evidence_by_id,
        )
        required_atoms = _criterion_atoms(criteria[criteria_id])
        if required_atoms:
            evidence_ids = [support["evidence_id"] for support in grounded_support if support["supported_atoms"]]
            if not supported_atoms:
                match_type, coverage = "insufficient", "weak"
            elif set(supported_atoms) == set(required_atoms):
                match_type, coverage = "direct", "strong"
            else:
                match_type, coverage = "inferred", "partial"
        normalised.append({
            "criteria_id": criteria_id,
            "matched_evidence": evidence_ids,
            "match_type": match_type,
            "coverage": coverage,
            "reasoning": str(item.get("reasoning") or "No matching explanation was returned.").strip(),
            **({"evidence_support": grounded_support} if required_atoms else {}),
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
        "matcher_rules_version": MATCHER_RULES_VERSION,
        "job_model_fingerprint": _fingerprint(job_model),
        "ckb_fingerprint": _fingerprint(ckb),
        "matches": normalised,
        "advertised_skill_tags": match_advertised_tags(job_model, {"matches": normalised}, ckb),
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
