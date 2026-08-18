import re

from .application_requirements import parse_application_requirements, parse_criteria_references
from .job_model import build_job_model


SEMANTIC_TYPES = {"primary_advertisement", "job_description_attachment", "application_instruction_attachment"}


def _value(source, field: str, default=""):
    return getattr(source, field, default) if not isinstance(source, dict) else source.get(field, default)


def _numbered_criteria(text: str) -> dict[str, str]:
    heading = re.search(r"(?im)^\s*(?:selection criteria|essential requirements|work related requirements)\s*:?[ \t]*$", text)
    if not heading:
        return {}
    section = text[heading.end():]
    next_heading = re.search(r"(?im)^\s*(?:desirable requirements?|how to apply|application instructions?|qualifications?|certification|approval)\s*:?[ \t]*$", section)
    if next_heading:
        section = section[:next_heading.start()]
    result: dict[str, str] = {}
    for match in re.finditer(r"(?ms)^\s*(\d+)\s*[.)]\s*(.+?)(?=^\s*\d+\s*[.)]\s|\Z)", section):
        text_value = re.sub(r"\s+", " ", match.group(2)).strip()
        if text_value:
            result[match.group(1)] = text_value
    return result


def _merge_requirements(base: dict, extra: dict) -> None:
    for name, document in extra["documents"].items():
        target = base["documents"][name]
        if document["requirement"] != "unknown":
            target["requirement"] = document["requirement"]
        preserve_embedded = target["format"].startswith("embedded_in_") and document["format"] == "not_applicable" and document["requirement"] == "not_required"
        if document["format"] != "unknown" and not preserve_embedded:
            target["format"] = document["format"]
        if document.get("limit"):
            target["limit"] = document["limit"]
        if name == "selection_criteria" and document.get("criteria_count") is not None:
            target["criteria_count"] = document["criteria_count"]
    base["additional_documents"] = list(dict.fromkeys([*base["additional_documents"], *extra["additional_documents"]]))
    base["warnings"] = list(dict.fromkeys([*base["warnings"], *extra["warnings"]]))
    if extra["source_excerpt"]:
        base["source_excerpt"] = " | ".join(filter(None, (base["source_excerpt"], extra["source_excerpt"])))[:2000]


def build_source_aware_models(application, sources: list) -> tuple[dict, dict]:
    semantic = [source for source in sources if _value(source, "source_type") in SEMANTIC_TYPES]
    usable = [source for source in semantic if _value(source, "extraction_status") == "extracted" and _value(source, "extracted_text").strip()]
    primary = next((source for source in usable if _value(source, "source_type") == "primary_advertisement"), None)
    primary_text = _value(primary, "extracted_text") or "\n".join(filter(None, (application.job_description, application.selection_criteria)))
    requirements = parse_application_requirements(primary_text)
    requirements.update(source="source_aware_parser", source_ids=[_value(primary, "source_id")] if primary else [])

    instruction_sources = [source for source in usable if _value(source, "source_type") == "application_instruction_attachment"]
    for source in instruction_sources:
        _merge_requirements(requirements, parse_application_requirements(_value(source, "extracted_text")))
        requirements["source_ids"].append(_value(source, "source_id"))

    reference_text = "\n".join([primary_text, *[_value(source, "extracted_text") for source in instruction_sources]])
    references = parse_criteria_references(reference_text)
    requirements["documents"]["selection_criteria"]["criteria_references"] = references
    if references:
        requirements["documents"]["selection_criteria"]["criteria_count"] = len(references)

    jdf_sources = [source for source in usable if _value(source, "source_type") == "job_description_attachment"]
    criteria_by_reference: dict[str, tuple[str, object]] = {}
    for source in jdf_sources:
        for reference, text in _numbered_criteria(_value(source, "extracted_text")).items():
            criteria_by_reference.setdefault(reference, (text, source))

    selected_references = references or list(criteria_by_reference)
    resolved = [(reference, *criteria_by_reference[reference]) for reference in selected_references if reference in criteria_by_reference]
    if resolved and requirements["documents"]["selection_criteria"]["criteria_count"] is None:
        requirements["documents"]["selection_criteria"]["criteria_count"] = len(resolved)
    missing = [reference for reference in references if reference not in criteria_by_reference]
    unresolved_jdf = [source for source in semantic if _value(source, "source_type") == "job_description_attachment" and source not in usable]
    if missing or (references and not jdf_sources):
        detail = f" criteria {', '.join(missing or references)}" if references else ""
        requirements["warnings"].append(f"The referenced JDF{detail} could not be resolved from a completely extracted source.")
        requirements["completeness"] = "incomplete"
    if unresolved_jdf:
        states = ", ".join(sorted({_value(source, "extraction_status") or _value(source, "acquisition_status") for source in unresolved_jdf}))
        requirements["warnings"].append(f"Job description source is not completely extracted ({states}).")
        requirements["completeness"] = "incomplete"

    formal_text = "\n".join(f"{reference}. {text}" for reference, text, _ in resolved)
    model = build_job_model(primary_text, formal_text or application.selection_criteria, application.position_title, application.company)
    provenance = {text: (reference, source) for reference, text, source in resolved}
    for criterion in model["criteria"]:
        match = provenance.get(criterion["criteria_text"])
        if match:
            reference, source = match
            criterion.update(
                source="job_description_attachment",
                source_id=_value(source, "source_id"),
                source_section="Selection Criteria",
                source_reference=reference,
            )
            if _value(source, "source_id") not in requirements["source_ids"]:
                requirements["source_ids"].append(_value(source, "source_id"))
    requirements["warnings"] = list(dict.fromkeys(requirements["warnings"]))
    requirements["review_status"] = "needs_confirmation"
    return requirements, model
