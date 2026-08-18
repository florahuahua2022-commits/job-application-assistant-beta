import hashlib
import re
from typing import Any


JOB_MODEL_SCHEMA_VERSION = "1.0"
CRITERION_CATEGORIES = {"behaviour", "technical", "knowledge", "qualification", "experience"}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t•▪■*-–—")


def _criterion_id(text: str) -> str:
    return f"C{hashlib.sha1(text.lower().encode('utf-8')).hexdigest()[:10].upper()}"


def is_selection_instruction(text: str) -> bool:
    cleaned = _clean(text)
    return bool(
        re.search(
            r"(?i)\b(?:applicants?|candidates?)\s+(?:must|should|are required to)\s+"
            r"(?:address|respond to|provide|submit).{0,80}\b(?:criteria|criterion|requirements?|responses?)\b",
            cleaned,
        )
        or re.search(r"(?i)\b(?:maximum|limit|no more than|not exceed|up to)\b.*\b(?:words?|pages?)\b", cleaned)
        or re.search(r"(?i)^(?:these|the following)\s+(?:criteria|responses?).*\b(?:addressed|answered|pages?|words?)\b", cleaned)
        or re.search(r"(?i)\bcover(?:ing)? letter\b.{0,100}\b(?:address|respond|criteria)\b", cleaned)
    )


def _meaningful_lines(value: str) -> list[str]:
    lines: list[str] = []
    for raw in value.splitlines():
        cleaned = _clean(re.sub(r"^\s*(?:\d+[.)]|[a-z][.)])\s*", "", raw, flags=re.IGNORECASE))
        if is_selection_instruction(cleaned):
            continue
        if len(cleaned) >= 12 and not re.fullmatch(r"(?i)(?:essential|desirable|selection criteria|requirements|responsibilities):?", cleaned):
            lines.append(cleaned)
    return lines


def _infer_requirement_lines(job_description: str) -> list[str]:
    candidates = _meaningful_lines(job_description)
    signals = re.compile(
        r"(?i)\b(?:demonstrated|experience|ability|knowledge|qualification|capability|skills?|"
        r"manage|coordinate|prepare|develop|deliver|communicat|stakeholder|must|essential|required)\b"
    )
    selected = [line for line in candidates if signals.search(line)]
    return (selected or candidates)[:12]


def _categories(text: str) -> list[str]:
    lowered = text.lower()
    result: list[str] = []
    category_signals = {
        "qualification": ("degree", "qualification", "certificate", "licence", "registration"),
        "experience": ("experience", "previously", "track record", "demonstrated"),
        "knowledge": ("knowledge", "understanding", "legislation", "policy", "framework"),
        "technical": ("system", "software", "technical", "data", "analysis", "report", "project", "finance", "procurement"),
        "behaviour": ("communicat", "stakeholder", "team", "lead", "organis", "adapt", "initiative", "interpersonal"),
    }
    for category, signals in category_signals.items():
        if any(signal in lowered for signal in signals):
            result.append(category)
    return result[:3] or ["behaviour"]


def _competencies(text: str) -> list[str]:
    competency_signals = {
        "stakeholder engagement": ("stakeholder", "relationship"),
        "communication": ("communicat", "briefing", "correspondence"),
        "project delivery": ("project", "program delivery"),
        "planning and organisation": ("organis", "planning", "priorit"),
        "problem solving": ("problem", "resolve", "judgement", "initiative"),
        "leadership": ("lead", "supervis", "manage a team"),
        "technical capability": ("technical", "system", "software", "data"),
        "policy and governance": ("policy", "governance", "legislation", "compliance"),
        "reporting": ("report", "analysis", "written advice"),
    }
    lowered = text.lower()
    found = [name for name, signals in competency_signals.items() if any(signal in lowered for signal in signals)]
    if not found:
        words = [word.lower() for word in re.findall(r"[A-Za-z][A-Za-z-]{3,}", text)]
        stop = {"with", "that", "this", "from", "your", "have", "will", "must", "ability", "demonstrated"}
        found = [word for word in words if word not in stop]
    return list(dict.fromkeys(found))[:5]


def _organisation_context(job_description: str, company: str) -> str:
    company_tokens = {
        word.lower() for word in re.findall(r"[A-Za-z0-9]+", company)
        if len(word) >= 3 and word.lower() not in {"the", "and", "pty", "ltd", "limited"}
    }
    fragments = [
        _clean(fragment)
        for fragment in re.split(r"(?:\r?\n+|(?<=[.!?])\s+)", job_description)
        if _clean(fragment)
    ]
    matching = [
        fragment for fragment in fragments
        if company_tokens and any(re.search(rf"(?i)\b{re.escape(token)}\b", fragment) for token in company_tokens)
    ]
    context = " | ".join(dict.fromkeys([company.strip(), *matching]))
    return context[:1600]


def parse_word_limits(text: str) -> dict[str, Any]:
    per_patterns = (
        r"(?i)(?:maximum|limit|no more than|up to)?\s*([\d,]{2,6})\s*words?\s*(?:per|for each)\s*(?:criterion|criteria|response)",
        r"(?i)(?:each|per)\s*(?:criterion|criteria|response)\s*(?:is|has|must be|should be|:)?.{0,20}?([\d,]{2,6})\s*words?",
    )
    total_patterns = (
        r"(?i)(?:total|overall|combined)(?:\s+(?:limit|maximum))?.{0,20}?([\d,]{2,6})\s*words?",
        r"(?i)(?:maximum|limit|no more than|not exceed|up to)\s*(?:of\s*)?([\d,]{2,6})\s*words?(?!\s*(?:per|for each))",
    )
    for pattern in per_patterns:
        match = re.search(pattern, text)
        if match:
            return {"limit_scope": "per_criteria", "per_criteria_word_limit": int(match.group(1).replace(",", "")), "total_word_limit": None, "limit_instruction": _clean(match.group(0))}
    for pattern in total_patterns:
        match = re.search(pattern, text)
        if match:
            return {"limit_scope": "total", "per_criteria_word_limit": None, "total_word_limit": int(match.group(1).replace(",", "")), "limit_instruction": _clean(match.group(0))}
    return {"limit_scope": "unspecified", "per_criteria_word_limit": None, "total_word_limit": None, "limit_instruction": ""}


def build_job_model(
    job_description: str,
    selection_criteria: str | None = None,
    position_title: str = "",
    company: str = "",
) -> dict[str, Any]:
    selection_text = (selection_criteria or "").strip()
    explicit_lines = _meaningful_lines(selection_text)
    looks_like_formal_criteria = bool(re.search(r"(?im)^\s*(?:selection criteria|\d+[.)]|essential\b|desirable\b)", selection_text))
    brief_guidance = bool(selection_text) and not looks_like_formal_criteria and len(selection_text) <= 220 and len(explicit_lines) <= 3
    if selection_text and not brief_guidance:
        lines, criterion_type, requirement_mode = explicit_lines, "essential", "explicit_selection_criteria"
    else:
        lines, criterion_type, requirement_mode = _infer_requirement_lines(job_description), "inferred", "inferred_requirements"
    criteria = []
    desirable_seen = False
    for line in lines:
        if re.search(r"(?i)\b(?:desirable|preferred|advantageous|highly regarded)\b", line):
            item_type = "desirable"
            desirable_seen = True
        else:
            item_type = criterion_type if not desirable_seen else ("desirable" if requirement_mode == "explicit_selection_criteria" else criterion_type)
        categories = _categories(line)
        criteria.append({
            "criteria_id": _criterion_id(line),
            "criteria_text": line,
            "criteria_type": item_type,
            "criterion_categories": categories,
            "primary_category": categories[0],
            "key_competencies": _competencies(line),
            "source": "selection_criteria" if requirement_mode == "explicit_selection_criteria" else "job_description",
        })
    limits = parse_word_limits(f"{job_description}\n{selection_text}")
    return {
        "schema_version": JOB_MODEL_SCHEMA_VERSION,
        "position_title": position_title.strip(),
        "organisation": company.strip(),
        "role_summary": _clean(job_description)[:800],
        "organisation_context": _organisation_context(job_description, company),
        "requirement_mode": requirement_mode,
        "brief_guidance": selection_text if brief_guidance else "",
        "criteria": criteria,
        **limits,
    }


def validate_job_model(model: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if model.get("schema_version") != JOB_MODEL_SCHEMA_VERSION:
        errors.append("Unsupported Job Model schema version.")
    if model.get("limit_scope") not in {"per_criteria", "total", "unspecified"}:
        errors.append("Invalid limit_scope.")
    for index, criterion in enumerate(model.get("criteria") or [], start=1):
        if criterion.get("criteria_type") not in {"essential", "desirable", "inferred"}:
            errors.append(f"Criterion {index} has an invalid criteria_type.")
        categories = set(criterion.get("criterion_categories") or [])
        if not categories or not categories <= CRITERION_CATEGORIES:
            errors.append(f"Criterion {index} has invalid categories.")
        if not str(criterion.get("criteria_text") or "").strip():
            errors.append(f"Criterion {index} has no criteria_text.")
        for field in ("source_id", "source_section", "source_reference"):
            if field in criterion and not isinstance(criterion[field], str):
                errors.append(f"Criterion {index} has invalid {field}.")
    return errors
