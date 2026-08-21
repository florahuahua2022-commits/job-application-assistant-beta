import hashlib
import json
import re
from typing import Any


CKB_SCHEMA_VERSION = "1.0"
EVIDENCE_TYPES = {
    "experience", "project", "volunteer", "education", "qualification", "award", "publication",
}

EMPLOYMENT_PERIOD_PATTERN = re.compile(
    r"(?i)\b((?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(?:19|20)\d{2})\s*(?:-|–|—|to)\s*"
    r"((?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(?:19|20)\d{2}|present|current|now))\b"
)


def stable_evidence_id(evidence_type: str, source_text: str) -> str:
    digest = hashlib.sha1(f"{evidence_type}|{source_text.strip()}".encode("utf-8")).hexdigest()[:12].upper()
    return f"EV{digest}"


def split_time_period(value: str) -> dict[str, str | None]:
    parts = re.split(r"\s*(?:-|–|—|to)\s*", value.strip(), maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return {"start": parts[0] or None, "end": parts[1] or None}
    return {"start": value.strip() or None, "end": None}


def _quality(item: dict[str, Any]) -> str:
    if str(item.get("result") or "").strip():
        return "high"
    if str(item.get("action") or item.get("responsibility") or "").strip():
        return "medium"
    return "low"


def experience_to_evidence(item: dict[str, Any]) -> dict[str, Any] | None:
    role = str(item.get("role_title") or "").strip()
    organisation = str(item.get("organization") or "").strip()
    action = str(item.get("responsibility") or item.get("action") or "").strip()
    situation = str(item.get("situation") or item.get("context") or "").strip()
    task = str(item.get("task") or "").strip()
    result = str(item.get("result") or "").strip()
    raw_source = str(item.get("source_text") or "").strip()
    source_text = raw_source or "\n".join(value for value in (role, organisation, situation, action, result) if value)
    if not source_text:
        return None
    date_text = str(item.get("time_period_text") or "").strip()
    if not date_text and situation.lower().startswith("employment dates:"):
        date_text = situation.split(":", 1)[1].strip()
    if not date_text:
        matches = EMPLOYMENT_PERIOD_PATTERN.findall(raw_source)
        if len(matches) == 1:
            date_text = f"{matches[0][0]} - {matches[0][1]}"
    supplied_period = item.get("time_period") or {}
    if not supplied_period.get("start") and not supplied_period.get("end"):
        supplied_period = split_time_period(date_text)
    date_status = "verified" if supplied_period.get("start") else "uncertain" if re.search(r"(?i)\b(?:19|20)\d{2}\b", raw_source) else "not_provided"
    evidence_type = str(item.get("evidence_type") or "experience").lower()
    if evidence_type not in EVIDENCE_TYPES:
        evidence_type = "experience"
    evidence_id = str(item.get("evidence_id") or "").strip() or stable_evidence_id(evidence_type, source_text)
    return {
        "schema_version": CKB_SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "source_section": str(item.get("source_section") or f"Work Experience > {organisation or 'Unknown organisation'} > {role or 'Unknown role'}"),
        "source_text": source_text,
        "time_period": supplied_period,
        "time_period_status": date_status,
        "situation": situation,
        "task": task,
        "action": action,
        "result": result,
        "detail": "",
        "competency_tags": list(item.get("competency_tags") or []),
        "evidence_quality": str(item.get("evidence_quality") or _quality(item)),
        "fact_verification": "explicit",
        "competency_inference": str(item.get("competency_inference") or "derived"),
    }


def _detail_evidence(source_text: str) -> list[dict[str, Any]]:
    heading_types = {
        "education": "education", "qualifications": "qualification", "qualification": "qualification",
        "certifications": "qualification", "certification": "qualification", "awards": "award", "award": "award",
        "publications": "publication", "publication": "publication", "projects": "project", "project": "project",
        "volunteering": "volunteer", "volunteer experience": "volunteer",
    }
    stop_headings = {"skills", "technical skills", "references", "referees", "work experience", "professional experience", "employment history"}
    lines = [re.sub(r"\s+", " ", line).strip(" •▪■*-\t") for line in source_text.splitlines()]
    lines = [line for line in lines if line]
    current_type: str | None = None
    current_heading = ""
    result: list[dict[str, Any]] = []
    for line in lines:
        lowered = line.lower().rstrip(":")
        if lowered in heading_types:
            current_type, current_heading = heading_types[lowered], line.rstrip(":")
            continue
        if lowered in stop_headings:
            current_type = None
            continue
        if not current_type or len(line) < 4:
            continue
        evidence_id = stable_evidence_id(current_type, line)
        is_star_type = current_type in {"project", "volunteer"}
        result.append({
            "schema_version": CKB_SCHEMA_VERSION,
            "evidence_id": evidence_id,
            "evidence_type": current_type,
            "source_section": current_heading,
            "source_text": line,
            "time_period": {"start": None, "end": None},
            "situation": "",
            "task": "",
            "action": line if is_star_type else "",
            "result": "",
            "detail": "" if is_star_type else line,
            "competency_tags": [],
            "evidence_quality": "medium" if len(line) >= 30 else "low",
            "fact_verification": "explicit",
            "competency_inference": "stated",
        })
    return result


def build_career_knowledge_base(source_text: str, experiences_json: str = "[]") -> list[dict[str, Any]]:
    try:
        experiences = json.loads(experiences_json or "[]")
    except (TypeError, json.JSONDecodeError):
        experiences = []
    evidence: list[dict[str, Any]] = []
    if isinstance(experiences, list):
        for item in experiences:
            if isinstance(item, dict):
                normalized = experience_to_evidence(item)
                if normalized:
                    evidence.append(normalized)
    evidence.extend(_detail_evidence(source_text))
    unique: dict[str, dict[str, Any]] = {}
    for item in evidence:
        unique.setdefault(item["evidence_id"], item)
    return list(unique.values())


def career_knowledge_base_is_current(items: Any) -> bool:
    return isinstance(items, list) and all(isinstance(item, dict) for item in items) and all(
        item.get("evidence_type") != "experience" or "time_period_status" in item
        for item in items
    )


def validate_career_knowledge_base(items: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    required = {"schema_version", "evidence_id", "evidence_type", "source_section", "source_text", "time_period", "evidence_quality", "fact_verification"}
    for index, item in enumerate(items):
        missing = sorted(required - set(item))
        if missing:
            errors.append(f"Item {index + 1} is missing: {', '.join(missing)}")
        if item.get("evidence_type") not in EVIDENCE_TYPES:
            errors.append(f"Item {index + 1} has an unsupported evidence_type.")
        if item.get("fact_verification") != "explicit":
            errors.append(f"Item {index + 1} is not explicitly verified.")
        if not str(item.get("source_text") or "").strip():
            errors.append(f"Item {index + 1} has no source_text.")
    return errors
