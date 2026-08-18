import json
import re
from typing import Any


APPLICATION_REQUIREMENTS_SCHEMA_VERSION = "1.0"
REVIEW_STATUSES = {"needs_confirmation", "confirmed", "user_overridden"}
REQUIREMENTS = {"required", "optional", "not_required", "unknown"}
FORMATS = {"standalone", "embedded_in_cover_letter", "embedded_in_resume", "portal_fields", "not_applicable", "unknown"}
LIMIT_UNITS = {"words", "characters", "pages"}
LIMIT_SCOPES = {"document", "per_criterion", "combined_documents"}
LIMIT_CONSTRAINTS = {"maximum", "minimum", "exact", "recommended"}
DOCUMENT_TYPES = ("resume", "cover_letter", "selection_criteria")


def _document(requirement: str = "unknown", format: str = "unknown", **extra: Any) -> dict[str, Any]:
    return {"requirement": requirement, "format": format, "limit": None, **extra}


def empty_application_requirements(source_text: str = "", source: str = "deterministic_parser") -> dict[str, Any]:
    return {
        "schema_version": APPLICATION_REQUIREMENTS_SCHEMA_VERSION,
        "review_status": "needs_confirmation",
        "source": source,
        "documents": {
            "resume": _document(),
            "cover_letter": _document(),
            "selection_criteria": _document(criteria_count=None),
        },
        "additional_documents": [],
        "source_text": source_text,
        "source_excerpt": "",
        "warnings": [],
    }


def _number(value: str) -> int:
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    cleaned = value.lower().strip().replace(",", "")
    return words.get(cleaned, int(cleaned) if cleaned.isdigit() else 0)


def _sentences(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", part).strip() for part in re.split(r"(?:\r?\n+|(?<=[.!?])\s+)", text) if part.strip()]


def _matches(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, re.IGNORECASE | re.DOTALL))


def _parse_limit(sentence: str) -> dict[str, Any] | None:
    match = re.search(
        r"(?i)\b(max(?:imum)?|no more than|not exceed|up to|min(?:imum)?|at least|exactly|recommended)?\s*"
        r"(?:of\s+)?(\d[\d,]*)\s*(words?|characters?|chars?|pages?)\b",
        sentence,
    )
    if match:
        qualifier, value, unit, source = match.group(1) or "exactly", match.group(2), match.group(3), match.group(0)
    else:
        match = re.search(r"(?i)\b(one|two|three|four|five|six|seven|eight|nine|ten)(?:\s*\(\d+\))?\s+(pages?)\b", sentence)
        if not match:
            return None
        qualifier = "maximum" if _matches(sentence, r"maximum|no more than|not exceed|up to") else "exactly"
        value, unit, source = match.group(1), match.group(2), match.group(0)
    qualifier = qualifier.lower()
    constraint = "maximum" if qualifier in {"max", "maximum", "no more than", "not exceed", "up to"} else "minimum" if qualifier in {"min", "minimum", "at least"} else "recommended" if qualifier == "recommended" else "exact"
    unit = unit.lower()
    scope = "per_criterion" if _matches(sentence, r"\b(?:per|each)\s+(?:criterion|criteria|response)\b|\b(?:criterion|response)\b.{0,20}\beach\b") else "document"
    return {
        "value": _number(value),
        "unit": "characters" if unit.startswith("char") else "words" if unit.startswith("word") else "pages",
        "scope": scope,
        "constraint": constraint,
        "source_text": source.strip(),
    }


def parse_application_requirements(source_text: str) -> dict[str, Any]:
    text = (source_text or "").strip()
    result = empty_application_requirements(text)
    if not text:
        result["warnings"].append("Application instructions were not supplied; document requirements remain unknown.")
        return result
    documents = result["documents"]
    relevant: list[str] = []
    for sentence in _sentences(text):
        lowered = sentence.lower()
        resume = bool(re.search(r"\b(?:cv|curriculum vitae|résumé|resume)\b", lowered))
        cover = "cover letter" in lowered or "covering letter" in lowered
        criteria = bool(re.search(r"\b(?:selection criteria|criterion|criteria|requirements?)\b", lowered))
        submit = bool(re.search(r"\b(?:submit|provide|include|attach|upload|address|respond)\b", lowered))
        negative = bool(re.search(r"\b(?:not required|do not (?:submit|provide|include|attach)|no .{0,60}(?:needed|required))\b", lowered))
        optional = "optional" in lowered
        if resume:
            relevant.append(sentence)
            if negative:
                documents["resume"].update(requirement="not_required", format="not_applicable")
            elif submit or _matches(sentence, r"\b(?:required|must)\b"):
                documents["resume"].update(requirement="required", format="standalone")
            elif optional:
                documents["resume"].update(requirement="optional", format="standalone")
        if cover:
            relevant.append(sentence)
            if negative:
                documents["cover_letter"].update(requirement="not_required", format="not_applicable")
            elif submit or _matches(sentence, r"\b(?:required|must)\b"):
                documents["cover_letter"].update(requirement="required", format="standalone")
            elif optional:
                documents["cover_letter"].update(requirement="optional", format="standalone")
        embedded = cover and criteria and _matches(sentence, r"(?:address|respond|responses?|include).{0,100}(?:in|within|through).{0,30}cover(?:ing)? letter|cover(?:ing)? letter.{0,100}(?:address|respond|criteria|requirements)")
        standalone = criteria and not cover and submit and _matches(sentence, r"\b(?:separate|standalone|statement|document|responses?|attachment|attach|submit|provide)\b")
        if embedded:
            documents["cover_letter"].update(requirement="required", format="standalone")
            documents["selection_criteria"].update(requirement="not_required", format="embedded_in_cover_letter")
            relevant.append(sentence)
        elif criteria and negative:
            documents["selection_criteria"].update(requirement="not_required", format="not_applicable")
            relevant.append(sentence)
        elif standalone:
            documents["selection_criteria"].update(requirement="required", format="standalone")
            relevant.append(sentence)
        limit = _parse_limit(sentence)
        if limit:
            target = "cover_letter" if cover else "selection_criteria" if criteria or limit["scope"] == "per_criterion" else None
            if target:
                documents[target]["limit"] = limit
                relevant.append(sentence)
                if limit["unit"] == "characters" and not _matches(sentence, r"(?:including|excluding|with|without)\s+spaces"):
                    result["warnings"].append(f"The {target.replace('_', ' ')} character limit does not specify whether spaces are included.")
    count = re.search(r"(?i)\b(?:address(?:ing)?|respond(?:ing)? to|response to)\s+(?:the\s+)?(?:following\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)(?:\s*\(\d+\))?\s+(?:selection\s+)?criteria\b", text)
    if count:
        documents["selection_criteria"]["criteria_count"] = _number(count.group(1))
    else:
        numbered = re.findall(r"(?m)^\s*(\d+)[.)]\s+.+$", text)
        if numbered and _matches(text, r"selection criteria|address the following criteria"):
            documents["selection_criteria"]["criteria_count"] = len(numbered)
    extras = re.findall(r"(?i)(?:submit|attach|provide|include)\s+(?:an?\s+|your\s+)?(portfolio|academic transcript|qualification certificate|referee report|writing sample)", text)
    result["additional_documents"] = list(dict.fromkeys(item.lower() for item in extras))
    result["source_excerpt"] = " | ".join(dict.fromkeys(relevant))[:2000]
    if all(item["requirement"] == "unknown" for item in documents.values()):
        result["warnings"].append("Submission document requirements could not be determined from the supplied text.")
    return result


def validate_application_requirements(model: dict[str, Any]) -> list[str]:
    if not isinstance(model, dict):
        return ["Application Requirements must be an object."]
    errors: list[str] = []
    if model.get("schema_version") != APPLICATION_REQUIREMENTS_SCHEMA_VERSION:
        errors.append("Unsupported Application Requirements schema version.")
    if model.get("review_status") not in REVIEW_STATUSES:
        errors.append("Invalid review_status.")
    documents = model.get("documents")
    if not isinstance(documents, dict):
        return errors + ["documents must be an object."]
    for name in DOCUMENT_TYPES:
        document = documents.get(name)
        if not isinstance(document, dict):
            errors.append(f"Missing {name} requirement.")
            continue
        requirement, format_value = document.get("requirement"), document.get("format")
        if requirement not in REQUIREMENTS:
            errors.append(f"Invalid {name} requirement.")
        if format_value not in FORMATS:
            errors.append(f"Invalid {name} format.")
        if requirement == "not_required" and format_value == "standalone":
            errors.append(f"{name} cannot be not_required and standalone.")
        if requirement == "required" and format_value == "not_applicable":
            errors.append(f"{name} cannot be required and not_applicable.")
        limit = document.get("limit")
        if limit is not None:
            if not isinstance(limit, dict):
                errors.append(f"Invalid {name} limit.")
            else:
                value = limit.get("value")
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    errors.append(f"Invalid {name} limit value.")
                if limit.get("unit") not in LIMIT_UNITS:
                    errors.append(f"Invalid {name} limit unit.")
                if limit.get("scope") not in LIMIT_SCOPES:
                    errors.append(f"Invalid {name} limit scope.")
                if limit.get("constraint") not in LIMIT_CONSTRAINTS:
                    errors.append(f"Invalid {name} limit constraint.")
    selection = documents.get("selection_criteria") or {}
    count = selection.get("criteria_count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        errors.append("criteria_count must be a non-negative integer or null.")
    if selection.get("format") == "embedded_in_cover_letter":
        cover = documents.get("cover_letter") or {}
        if cover.get("requirement") == "not_required" or cover.get("format") == "not_applicable":
            errors.append("Selection criteria cannot be embedded in a cover letter that is not required.")
    return errors


def legacy_application_requirements(selection_criteria: str | None = None) -> dict[str, Any]:
    model = empty_application_requirements(selection_criteria or "", source="legacy_inference")
    model["documents"]["resume"].update(requirement="required", format="standalone")
    model["documents"]["cover_letter"].update(requirement="required", format="standalone")
    if (selection_criteria or "").strip():
        model["documents"]["selection_criteria"].update(requirement="required", format="standalone")
    else:
        model["documents"]["selection_criteria"].update(requirement="not_required", format="not_applicable")
    model["warnings"].append("Requirements were inferred from the legacy fixed-pack behaviour and must be confirmed.")
    return model


def load_application_requirements(raw_json: str | None, selection_criteria: str | None = None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return legacy_application_requirements(selection_criteria)
    return parsed if parsed and not validate_application_requirements(parsed) else legacy_application_requirements(selection_criteria)
