import json
import re
from copy import deepcopy
from typing import Any


APPLICATION_REQUIREMENTS_SCHEMA_VERSION = "1.0"
REVIEW_STATUSES = {"needs_confirmation", "confirmed", "user_overridden"}
REQUIREMENTS = {"required", "optional", "not_required", "unknown"}
FORMATS = {"standalone", "embedded_in_cover_letter", "embedded_in_resume", "portal_fields", "not_applicable", "unknown"}
BASES = {"employer_explicit", "user_confirmed", "product_default", "unknown"}
LIMIT_UNITS = {"words", "characters", "pages"}
LIMIT_SCOPES = {"document", "per_criterion", "combined_documents"}
LIMIT_CONSTRAINTS = {"maximum", "minimum", "exact", "recommended"}
DOCUMENT_TYPES = ("resume", "cover_letter", "selection_criteria")
DOCUMENT_FORMATS = {
    "resume": {"standalone", "portal_fields", "not_applicable", "unknown"},
    "cover_letter": {"standalone", "portal_fields", "not_applicable", "unknown"},
    "selection_criteria": FORMATS,
}


def _document(requirement: str = "unknown", format: str = "unknown", basis: str = "unknown", **extra: Any) -> dict[str, Any]:
    return {"requirement": requirement, "format": format, "basis": basis, "limit": None, **extra}


def empty_application_requirements(source_text: str = "", source: str = "deterministic_parser") -> dict[str, Any]:
    return {
        "schema_version": APPLICATION_REQUIREMENTS_SCHEMA_VERSION,
        "review_status": "needs_confirmation",
        "source": source,
        "documents": {
            "resume": _document(),
            "cover_letter": _document(),
            "selection_criteria": _document(criteria_count=None, criteria_references=[]),
        },
        "additional_documents": [],
        "source_text": source_text,
        "source_excerpt": "",
        "source_ids": [],
        "completeness": "complete",
        "warnings": [],
    }


def _number(value: str) -> int:
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    cleaned = value.lower().strip().replace(",", "")
    return words.get(cleaned, int(cleaned) if cleaned.isdigit() else 0)


def _sentences(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", part).strip() for part in re.split(r"(?:\r?\n+|(?<!\d\.)(?<=[.!?])\s+)", text) if part.strip()]


def _matches(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, re.IGNORECASE | re.DOTALL))


def _parse_limit(sentence: str) -> dict[str, Any] | None:
    match = re.search(
        r"(?i)\b(max(?:imum)?|no more than|not exceed|up to|min(?:imum)?|at least|exactly|recommended)?\s*"
        r"(?:of\s+)?(\d[\d,]*)[-\s]*(words?|characters?|chars?|pages?)\b",
        sentence,
    )
    if match:
        qualifier, value, unit, source = match.group(1) or "exactly", match.group(2), match.group(3), match.group(0)
    else:
        match = re.search(r"(?i)\b(max(?:imum)?|no more than|not exceed|up to|min(?:imum)?|at least|exactly|recommended)?\s*(one|two|three|four|five|six|seven|eight|nine|ten)(?:\s*\(\d+\))?[-\s]+(pages?)\b", sentence)
        if not match:
            return None
        qualifier, value, unit, source = match.group(1) or "exactly", match.group(2), match.group(3), match.group(0)
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


def parse_criteria_references(text: str) -> list[str]:
    result: list[str] = []
    pattern = r"(?i)\b(?:selection\s+)?criter(?:ion|ia)\s+((?:\d+\s*(?:(?:,|&|and|to|[-–—])\s*)?)+)"
    for match in re.finditer(pattern, text):
        value = match.group(1)
        for start, end in re.findall(r"(\d+)\s*(?:-|–|—|to)\s*(\d+)", value, re.IGNORECASE):
            for number in range(int(start), int(end) + 1):
                if str(number) not in result:
                    result.append(str(number))
        ranged = re.sub(r"\d+\s*(?:-|–|—|to)\s*\d+", "", value, flags=re.IGNORECASE)
        for number in re.findall(r"\d+", ranged):
            if number not in result:
                result.append(number)
    return result


def parse_application_requirements(source_text: str) -> dict[str, Any]:
    text = (source_text or "").strip()
    result = empty_application_requirements(text)
    if not text:
        result["warnings"].append("Application instructions were not supplied; document requirements remain unknown.")
        return result
    documents = result["documents"]
    relevant: list[str] = []
    provide_list = False
    for sentence in _sentences(text):
        if re.fullmatch(r"(?i)(?:please\s+)?provide(?:\s+the\s+following)?\s*:", sentence):
            provide_list = True
            continue
        numbered_material = bool(re.match(r"^\d+[.)]\s+", sentence))
        effective_sentence = f"Provide {sentence}" if provide_list and numbered_material else sentence
        lowered = sentence.lower()
        resume = bool(re.search(r"\b(?:cv|curriculum vitae|résumé|resume)\b", lowered))
        cover = "cover letter" in lowered or "covering letter" in lowered
        criteria = bool(re.search(r"\b(?:selection criteria|criterion|criteria|requirements?)\b", lowered))
        submit = bool(re.search(r"\b(?:submit|provide|include|attach|upload|address|respond)\b", effective_sentence, re.IGNORECASE))
        negative = bool(re.search(r"\b(?:not required|do not (?:submit|provide|include|attach)|no .{0,60}(?:needed|required))\b", lowered))
        optional = "optional" in lowered
        if resume:
            relevant.append(sentence)
            if negative:
                documents["resume"].update(requirement="not_required", format="not_applicable", basis="employer_explicit")
            elif submit or _matches(sentence, r"\b(?:required|must)\b"):
                documents["resume"].update(requirement="required", format="standalone", basis="employer_explicit")
            elif optional:
                documents["resume"].update(requirement="optional", format="standalone", basis="employer_explicit")
        if cover:
            relevant.append(sentence)
            if negative:
                documents["cover_letter"].update(requirement="not_required", format="not_applicable", basis="employer_explicit")
            elif submit or _matches(sentence, r"\b(?:required|must)\b"):
                documents["cover_letter"].update(requirement="required", format="standalone", basis="employer_explicit")
            elif optional:
                documents["cover_letter"].update(requirement="optional", format="standalone", basis="employer_explicit")
        embedded = cover and criteria and _matches(sentence, r"(?:address|respond|responses?|include).{0,100}(?:in|within|through).{0,30}cover(?:ing)? letter|cover(?:ing)? letter.{0,100}(?:address|respond|criteria|requirements)")
        standalone = criteria and not cover and submit and _matches(sentence, r"\b(?:separate|standalone|statement|document|responses?|attachment|attach|submit|provide)\b")
        if embedded:
            documents["cover_letter"].update(requirement="required", format="standalone", basis="employer_explicit")
            documents["selection_criteria"].update(requirement="not_required", format="embedded_in_cover_letter", basis="employer_explicit")
            relevant.append(sentence)
        elif criteria and negative:
            if documents["selection_criteria"]["format"] == "embedded_in_cover_letter" and "separate" in lowered:
                documents["selection_criteria"]["requirement"] = "not_required"
            else:
                documents["selection_criteria"].update(requirement="not_required", format="not_applicable", basis="employer_explicit")
            relevant.append(sentence)
        elif standalone:
            documents["selection_criteria"].update(requirement="required", format="standalone", basis="employer_explicit")
            relevant.append(sentence)
        limit = _parse_limit(sentence)
        if limit:
            target = "cover_letter" if cover else "selection_criteria" if criteria or limit["scope"] == "per_criterion" or ("response" in lowered and documents["selection_criteria"]["requirement"] == "required") else None
            if target:
                documents[target]["limit"] = limit
                relevant.append(sentence)
                if limit["unit"] == "characters" and not _matches(sentence, r"(?:including|excluding|with|without)\s+spaces"):
                    result["warnings"].append(f"The {target.replace('_', ' ')} character limit does not specify whether spaces are included.")
    references = parse_criteria_references(text)
    documents["selection_criteria"]["criteria_references"] = references
    count = re.search(r"(?i)\b(?:address(?:ing)?|respond(?:ing)? to|response to)\s+(?:the\s+)?(?:following\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)(?:\s*\(\d+\))?\s+(?:selection\s+)?criteria\b", text)
    if references:
        documents["selection_criteria"]["criteria_count"] = len(references)
    elif count:
        documents["selection_criteria"]["criteria_count"] = _number(count.group(1))
    else:
        numbered = re.findall(r"(?m)^\s*(\d+)[.)]\s+.+$", text)
        if numbered and _matches(text, r"(?im)^\s*selection criteria\s*:?\s*$"):
            documents["selection_criteria"]["criteria_count"] = len(numbered)
    extras = re.findall(r"(?i)(?:submit|attach|provide|include)\s+(?:an?\s+|your\s+)?(portfolio|academic transcript|qualification certificate|referee report|writing sample)", text)
    result["additional_documents"] = list(dict.fromkeys(item.lower() for item in extras))
    if documents["resume"]["requirement"] == "required" and documents["cover_letter"]["requirement"] == "required" and documents["selection_criteria"]["requirement"] == "unknown":
        documents["selection_criteria"].update(requirement="not_required", format="not_applicable", basis="product_default")
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
    for field in ("source", "source_text", "source_excerpt"):
        if not isinstance(model.get(field), str):
            errors.append(f"{field} must be a string.")
    source_ids = model.get("source_ids", [])
    if not isinstance(source_ids, list) or any(not isinstance(item, str) for item in source_ids):
        errors.append("source_ids must be a list of strings.")
    if model.get("completeness", "complete") not in {"complete", "incomplete"}:
        errors.append("Invalid completeness.")
    warnings = model.get("warnings")
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        errors.append("warnings must be a list of strings.")
    additional_documents = model.get("additional_documents")
    if not isinstance(additional_documents, list) or any(not isinstance(item, str) for item in additional_documents):
        errors.append("additional_documents must be a list of strings.")
    documents = model.get("documents")
    if not isinstance(documents, dict):
        return errors + ["documents must be an object."]
    unknown_documents = set(documents) - set(DOCUMENT_TYPES)
    if unknown_documents:
        errors.append("documents contains unsupported document types.")
    for name in DOCUMENT_TYPES:
        document = documents.get(name)
        if not isinstance(document, dict):
            errors.append(f"Missing {name} requirement.")
            continue
        allowed_document_fields = {"requirement", "format", "basis", "limit"}
        if name == "selection_criteria":
            allowed_document_fields.update({"criteria_count", "criteria_references"})
        if set(document) - allowed_document_fields:
            errors.append(f"{name} contains unsupported fields.")
        requirement, format_value = document.get("requirement"), document.get("format")
        if document.get("basis", "unknown") not in BASES:
            errors.append(f"Invalid {name} basis.")
        if requirement not in REQUIREMENTS:
            errors.append(f"Invalid {name} requirement.")
        if format_value not in DOCUMENT_FORMATS[name]:
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
                if set(limit) - {"value", "unit", "scope", "constraint", "source_text"}:
                    errors.append(f"{name} limit contains unsupported fields.")
                value = limit.get("value")
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    errors.append(f"Invalid {name} limit value.")
                if limit.get("unit") not in LIMIT_UNITS:
                    errors.append(f"Invalid {name} limit unit.")
                if limit.get("scope") not in LIMIT_SCOPES:
                    errors.append(f"Invalid {name} limit scope.")
                if limit.get("constraint") not in LIMIT_CONSTRAINTS:
                    errors.append(f"Invalid {name} limit constraint.")
                if not isinstance(limit.get("source_text"), str):
                    errors.append(f"Invalid {name} limit source_text.")
    selection = documents.get("selection_criteria") or {}
    count = selection.get("criteria_count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        errors.append("criteria_count must be a non-negative integer or null.")
    references = selection.get("criteria_references", [])
    if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
        errors.append("criteria_references must be a list of strings.")
    if selection.get("format") == "embedded_in_cover_letter":
        cover = documents.get("cover_letter") or {}
        if cover.get("requirement") == "not_required" or cover.get("format") == "not_applicable":
            errors.append("Selection criteria cannot be embedded in a cover letter that is not required.")
    if selection.get("format") == "embedded_in_resume":
        resume = documents.get("resume") or {}
        if resume.get("requirement") == "not_required" or resume.get("format") == "not_applicable":
            errors.append("Selection criteria cannot be embedded in a resume that is not required.")
    return errors


def confirm_application_requirements(model: dict[str, Any]) -> dict[str, Any]:
    if material_requirements_unknown(model):
        raise ValueError("Resolve the unknown document requirements and formats before confirming them.")
    confirmed = deepcopy(model)
    for document in confirmed["documents"].values():
        document.setdefault("basis", "unknown")
    confirmed["review_status"] = "confirmed"
    errors = validate_application_requirements(confirmed)
    if errors:
        raise ValueError(errors[0])
    return confirmed


def correct_application_requirements(
    model: dict[str, Any],
    documents: dict[str, Any],
    additional_documents: list[str],
) -> dict[str, Any]:
    corrected = deepcopy(model)
    corrected["documents"] = deepcopy(documents)
    for name, document in corrected["documents"].items():
        previous = (model.get("documents") or {}).get(name) or {}
        if document.get("requirement") != "unknown" and (
            document.get("requirement") != previous.get("requirement")
            or document.get("format") != previous.get("format")
            or previous.get("basis", "unknown") == "unknown"
        ):
            document["basis"] = "user_confirmed"
        else:
            document.setdefault("basis", previous.get("basis", "unknown"))
    corrected["additional_documents"] = list(additional_documents)
    corrected["review_status"] = "user_overridden"
    if not material_requirements_unknown(corrected):
        corrected["warnings"] = [
            warning for warning in corrected.get("warnings") or []
            if warning != "Submission document requirements could not be determined from the supplied text."
            and "requirements remain unknown" not in warning.lower()
        ]
    errors = validate_application_requirements(corrected)
    if errors:
        raise ValueError(errors[0])
    return corrected


def material_requirements_unknown(model: dict[str, Any]) -> bool:
    for document in (model.get("documents") or {}).values():
        requirement = document.get("requirement")
        if requirement == "unknown" or requirement in {"required", "optional"} and document.get("format") == "unknown":
            return True
    return False


def normalise_requirements_source(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\r\n", "\n").replace("\r", "\n")).strip()


def requirements_source_changed(
    old_job_description: str | None,
    old_selection_criteria: str | None,
    new_job_description: str | None,
    new_selection_criteria: str | None,
) -> bool:
    return (
        normalise_requirements_source(old_job_description) != normalise_requirements_source(new_job_description)
        or normalise_requirements_source(old_selection_criteria) != normalise_requirements_source(new_selection_criteria)
    )


def legacy_application_requirements(selection_criteria: str | None = None) -> dict[str, Any]:
    model = empty_application_requirements(selection_criteria or "", source="legacy_inference")
    model["documents"]["resume"].update(requirement="required", format="standalone", basis="product_default")
    model["documents"]["cover_letter"].update(requirement="required", format="standalone", basis="product_default")
    if (selection_criteria or "").strip():
        model["documents"]["selection_criteria"].update(requirement="required", format="standalone", basis="product_default")
    else:
        model["documents"]["selection_criteria"].update(requirement="not_required", format="not_applicable", basis="product_default")
    model["warnings"].append("Requirements were inferred from the legacy fixed-pack behaviour and must be confirmed.")
    return model


def load_application_requirements(raw_json: str | None, selection_criteria: str | None = None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return legacy_application_requirements(selection_criteria)
    if not parsed:
        return legacy_application_requirements(selection_criteria)
    for document in (parsed.get("documents") or {}).values():
        document.setdefault("basis", "unknown")
    if validate_application_requirements(parsed):
        return legacy_application_requirements(selection_criteria)
    if parsed.get("review_status") == "confirmed" and material_requirements_unknown(parsed):
        parsed["review_status"] = "needs_confirmation"
    return parsed
