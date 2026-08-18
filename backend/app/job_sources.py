import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


JOB_SOURCE_SCHEMA_VERSION = "1.0"
SOURCE_TYPES = {
    "primary_advertisement", "job_description_attachment", "application_instruction_attachment",
    "mandatory_form", "other_supporting_attachment", "unknown_attachment",
}
ACQUISITION_STATUSES = {"discovered", "fetched", "uploaded", "unavailable", "failed", "requires_auth", "unsupported"}
EXTRACTION_STATUSES = {"not_attempted", "extracted", "partial", "failed", "not_applicable"}
CLASSIFICATION_CONFIDENCES = {"high", "medium", "low"}


def validate_job_source(source: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if source.get("schema_version") != JOB_SOURCE_SCHEMA_VERSION:
        errors.append("Unsupported Job Source schema version.")
    if source.get("source_type") not in SOURCE_TYPES:
        errors.append("Invalid Job Source source_type.")
    if source.get("acquisition_status") not in ACQUISITION_STATUSES:
        errors.append("Invalid Job Source acquisition_status.")
    if source.get("extraction_status") not in EXTRACTION_STATUSES:
        errors.append("Invalid Job Source extraction_status.")
    if source.get("classification_confidence") not in CLASSIFICATION_CONFIDENCES:
        errors.append("Invalid Job Source classification_confidence.")
    for field in ("source_id", "title", "label", "extracted_text", "classification_reasons_json", "warnings_json"):
        if not isinstance(source.get(field), str):
            errors.append(f"Job Source {field} must be a string.")
    for field in ("classification_reasons_json", "warnings_json"):
        try:
            value = json.loads(source.get(field, ""))
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError
        except (json.JSONDecodeError, TypeError, ValueError):
            errors.append(f"Job Source {field} must contain a JSON list of strings.")
    return errors


def _hash(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _canonical_url_hash(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    return _hash(urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")))


def _normalise_signal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _classify_discovery(discovery: dict[str, Any], primary_source_id: str) -> dict[str, Any] | None:
    url = str(discovery.get("url") or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    label = str(discovery.get("label") or "").strip()
    title_attr = str(discovery.get("title") or "").strip()
    filename = str(discovery.get("filename") or parsed.path.rsplit("/", 1)[-1]).strip()
    context = str(discovery.get("context") or "").strip()[:1000]
    fields = {
        "label/title": _normalise_signal(f"{label} {title_attr}"),
        "URL/filename": _normalise_signal(f"{parsed.path} {filename}"),
        "context": _normalise_signal(context),
    }
    if re.search(r"\b(?:privacy|accessibility|annual report|corporate plan|logo|terms of use|cookie policy)\b", f'{fields["label/title"]} {fields["URL/filename"]}'):
        return None
    classifications = (
        ("job_description_attachment", "Job Description Form (JDF)", r"\b(?:jdf|job description form)\b"),
        ("job_description_attachment", "Position description", r"\b(?:position description(?: form)?|role description)\b"),
        ("application_instruction_attachment", "Application information pack", r"\b(?:(?:application|applicant|candidate)(?: information)? pack|application information|applicant information)\b"),
        ("mandatory_form", "Mandatory application form", r"\b(?:consent form|integrity and qualification|declaration|referee form|eligibility form)\b"),
        ("other_supporting_attachment", "Supporting attachment", r"\b(?:organisational chart|organizational chart|information sheet|brochure)\b"),
    )
    source_type = source_title = matched_field = matched_text = None
    for candidate_type, candidate_title, pattern in classifications:
        for field, value in fields.items():
            match = re.search(pattern, value)
            if match:
                source_type, source_title, matched_field, matched_text = candidate_type, candidate_title, field, match.group(0)
                break
        if source_type:
            break
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if not source_type:
        if extension not in {"pdf", "doc", "docx", "txt"} and not re.search(r"\b(?:attachment|download)\b", fields["context"]):
            return None
        source_type, source_title, matched_field, matched_text = "unknown_attachment", label or title_attr or filename or "Attachment", "file/context", extension or "attachment"
    confidence = "high" if matched_field == "label/title" else "medium" if source_type != "unknown_attachment" else "low"
    content_type = {"pdf": "application/pdf", "doc": "application/msword", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "txt": "text/plain"}.get(extension)
    return _source(
        source_type=source_type,
        title=source_title,
        label=label or title_attr or filename,
        source_url=url,
        discovered_from_source_id=primary_source_id,
        discovered_from_url=str(discovery.get("discovered_from_url") or "") or None,
        content_type=content_type,
        filename=filename or None,
        acquisition_status="discovered",
        extraction_status="not_attempted",
        extracted_text="",
        canonical_url_hash=_canonical_url_hash(url),
        classification_confidence=confidence,
        classification_reasons_json=json.dumps([f"{matched_field}: {matched_text}"]),
        discovery_context=context,
    )


def _source(**values: Any) -> dict[str, Any]:
    source = {
        "schema_version": JOB_SOURCE_SCHEMA_VERSION,
        "source_id": str(uuid4()),
        "title": "",
        "label": "",
        "source_url": None,
        "discovered_from_source_id": None,
        "discovered_from_url": None,
        "content_type": None,
        "filename": None,
        "extracted_text": "",
        "content_sha256": None,
        "canonical_url_hash": None,
        "classification_reasons_json": "[]",
        "warnings_json": "[]",
        "discovery_context": "",
        **values,
    }
    errors = validate_job_source(source)
    if errors:
        raise ValueError(errors[0])
    return source


def build_job_sources(advertisement_text: str, source_url: str | None = None, discoveries: list[dict] | None = None) -> list[dict[str, Any]]:
    text = (advertisement_text or "").strip()
    primary = _source(
        source_type="primary_advertisement",
        title="Job advertisement",
        label="Primary advertisement",
        source_url=source_url,
        content_type="text/plain",
        acquisition_status="fetched",
        extraction_status="extracted",
        extracted_text=text,
        content_sha256=_hash(text),
        canonical_url_hash=_canonical_url_hash(source_url),
        classification_confidence="high",
        classification_reasons_json=json.dumps(["application input"]),
    )
    patterns = (
        ("job_description_attachment", "Job Description Form (JDF)", r"(?i)\b(?:attached\s+(?:JDF|Job Description Form)|(?:refer\s+to|see)(?:\s+the)?(?:\s+attached)?\s+(?:JDF|Job Description Form)|criteria\b.{0,80}\battached\s+(?:JDF|Job Description Form))\b"),
        ("job_description_attachment", "Position description", r"(?i)\b(?:attached|refer\s+to|see)(?:\s+the)?(?:\s+attached)?\s+(?:position|role)\s+description\b"),
        ("application_instruction_attachment", "Application information pack", r"(?i)\b(?:attached\s+|refer\s+to(?:\s+the)?\s+|see(?:\s+the)?\s+)?(?:application|applicant|candidate)(?:\s+information)?\s+pack\b"),
    )
    sources = [primary]
    for source_type, title, pattern in patterns:
        match = re.search(pattern, text)
        if not match or any(item["source_type"] == source_type and item["title"] == title for item in sources):
            continue
        excerpt = re.sub(r"\s+", " ", text[max(0, match.start() - 100):match.end() + 100]).strip()
        sources.append(_source(
            source_type=source_type,
            title=title,
            label=match.group(0),
            discovered_from_source_id=primary["source_id"],
            discovered_from_url=source_url,
            acquisition_status="discovered",
            extraction_status="not_attempted",
            classification_confidence="high",
            classification_reasons_json=json.dumps([f"explicit reference: {match.group(0)}"]),
            warnings_json=json.dumps([f"Referenced {title} has not been acquired. Source text: {excerpt}"]),
        ))
    discovered: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for discovery in discoveries or []:
        item = _classify_discovery(discovery, primary["source_id"])
        if not item or not item["canonical_url_hash"] or item["canonical_url_hash"] in seen_urls:
            continue
        seen_urls.add(item["canonical_url_hash"])
        discovered.append(item)
    discovered.sort(key=lambda item: item["source_type"] == "unknown_attachment")
    discovered = discovered[:10]
    if discovered:
        keys = {(item["source_type"], item["title"]) for item in discovered}
        sources = [item for item in sources if item is primary or (item["source_type"], item["title"]) not in keys]
    return sources + discovered
