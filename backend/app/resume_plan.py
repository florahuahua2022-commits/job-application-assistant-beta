import json
import re
from datetime import datetime
from collections import defaultdict
from typing import Any
from .ckb import evidence_density
from .resume_timeline import apply_timeline, timeline_text


RESUME_PLAN_SCHEMA_VERSION = "2.0"


def _normalise_identity_text(value: Any) -> str:
    value = str(value or "").casefold().replace("–", "-").replace("—", "-")
    value = re.sub(r"[#*_`~|•▪■]+", " ", value)
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def _contains_identity(line: str, marker: str) -> bool:
    return bool(marker and f" {marker} " in f" {line} ")


def _role_identity_positions(lines: list[str], role: dict[str, Any]) -> list[int]:
    role_marker = _normalise_identity_text(role.get("role_marker"))
    employer_marker = _normalise_identity_text(role.get("employer_marker"))
    if not employer_marker:  # Backward compatibility for historical pre-4C.2 plans.
        return [index for index, line in enumerate(lines) if _contains_identity(line, role_marker)]
    positions = set()
    for index, line in enumerate(lines):
        if _contains_identity(line, role_marker) and _contains_identity(line, employer_marker):
            positions.add(index)
        if index + 1 < len(lines):
            next_line = lines[index + 1]
            if (
                (_contains_identity(line, role_marker) and _contains_identity(next_line, employer_marker))
                or (_contains_identity(line, employer_marker) and _contains_identity(next_line, role_marker))
            ):
                positions.add(index)
    return sorted(positions)


def validate_resume_content(content: str, plan: dict[str, Any], evidence_used: list[str]) -> dict[str, Any]:
    issues = []
    for section in plan.get("required_sections") or []:
        if not re.search(rf"(?im)^#+\s*{re.escape(str(section))}\s*$", content):
            issues.append({"code": "missing_required_section", "message": f"The CV is missing the {section} section."})
    word_count = len(re.findall(r"\b[\w'-]+\b", content, flags=re.UNICODE))
    if plan.get("maximum_words") and word_count > int(plan["maximum_words"]):
        issues.append({"code": "resume_too_long", "message": "The CV exceeds the employer's explicit word limit."})
    if set(map(str, evidence_used)) - selected_resume_evidence_ids(plan):
        issues.append({"code": "unselected_evidence_used", "message": "The CV uses evidence outside the Resume Curation Plan."})
    normalised_lines = [_normalise_identity_text(line) for line in content.splitlines()]
    normalised_lines = [line for line in normalised_lines if line]
    visible_roles = [role for role in plan.get("roles") or [] if role.get("include_role_header")]
    candidates = {id(role): _role_identity_positions(normalised_lines, role) for role in visible_roles}
    positions = {id(role): values[0] if len(values) == 1 else -1 for role in visible_roles for values in [candidates[id(role)]]}
    role_positions = []
    for role in visible_roles:
        marker = str(role.get("role_marker") or "").strip()
        position = positions[id(role)] if marker else -1
        if len(candidates[id(role)]) > 1:
            issues.append({"code": "ambiguous_role_header", "message": f"The CV contains an ambiguous employment header for {marker}."})
        elif marker and position < 0:
            issues.append({"code": "missing_role_header", "message": f"The CV is missing the required role header: {role['role_marker']}."})
        elif position >= 0:
            role_positions.append(position)
        period = _normalise_identity_text(role.get("display_period"))
        later_positions = [value for value in positions.values() if value > position]
        role_block = " ".join(normalised_lines[position:min(later_positions, default=len(normalised_lines))]) if position >= 0 else ""
        if period and not _contains_identity(role_block, period):
            issues.append({"code": "missing_role_period", "message": f"The CV is missing the authoritative employment period for {role.get('role_marker') or role.get('source_section')}."})
    expected_timeline = timeline_text(plan)
    if expected_timeline and _normalise_identity_text(expected_timeline) not in _normalise_identity_text(content):
        issues.append({"code": "missing_timeline", "message": "The CV omits required timeline-only employment dates. Restore the Additional Experience lines from the plan."})
    if len(role_positions) > 1 and role_positions != sorted(role_positions):
        issues.append({"code": "role_order_mismatch", "message": "The CV role headers do not follow reverse chronological Resume Plan order."})
    return {"valid": not issues, "word_count": word_count, "issues": issues}


def evaluate_resume_quality(content: str, plan: dict[str, Any]) -> dict[str, Any]:
    """Apply cheap, objective quality checks after factual review."""
    issues = []
    target_words = int(plan.get("target_words") or 0)
    word_count = len(re.findall(r"\b[\w'-]+\b", content, flags=re.UNICODE))
    if target_words and word_count < target_words * .7:
        eligible = [item for item in plan.get("selected_evidence") or [] if item.get("evidence_type") == "experience"]
        thin = [item for item in eligible if item.get("evidence_thin") is True]
        source_is_thin = bool(eligible) and len(thin) / len(eligible) > .5
        issues.append({
            "type": "insufficient_source_detail" if source_is_thin else "concise_but_relevant",
            "severity": "major" if source_is_thin else "advisory", "blocks_release": source_is_thin,
            "description": "Relevant source evidence is too thin to support a useful tailored CV." if source_is_thin else
                           f"The CV contains {word_count} words. Length alone does not establish missing content.",
            "recommended_action": "Add confirmed actions, objects, tools or scope to the relevant experiences: " +
                                  "; ".join(sorted({item["source_section"] for item in thin})) if source_is_thin else
                                  "Keep the concise draft; do not expand low or timeline-only roles for length.",
        })

    work_section = re.search(r"(?ims)^##\s*Work Experience\s*$\n(.*?)(?=^##\s|\Z)", content)
    bullets = re.findall(r"(?im)^\s*[-*]\s+([A-Za-z][A-Za-z'-]*)\b", work_section.group(1) if work_section else "")
    if len(bullets) >= 3:
        counts = {verb.lower(): sum(item.lower() == verb.lower() for item in bullets) for verb in bullets}
        repeated_verb, repeated_count = max(counts.items(), key=lambda item: item[1])
        if repeated_count / len(bullets) > .5:
            issues.append({
                "type": "resume_repetitive_opening", "severity": "advisory", "blocks_release": False,
                "description": f"'{repeated_verb.title()}' opens {repeated_count} of {len(bullets)} work-experience bullets.",
            })
    return {"status": "fail" if any(item.get("blocks_release") for item in issues) else "pass", "word_count": word_count, "issues": issues}


def selected_resume_evidence_ids(plan: dict[str, Any]) -> set[str]:
    return {str(item.get("evidence_id")) for item in plan.get("selected_evidence") or [] if item.get("evidence_id")}


def resume_evidence_pack(ckb_json: str, plan_json: str) -> list[dict[str, Any]]:
    try:
        ckb = json.loads(ckb_json or "[]")
        plan = json.loads(plan_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return []
    by_id = {str(item.get("evidence_id")): item for item in ckb if isinstance(item, dict)}
    selected = selected_resume_evidence_ids(plan)
    role_ids = [str(evidence_id) for role in plan.get("roles") or [] for evidence_id in role.get("selected_evidence_ids") or []]
    ordered_ids = role_ids + [str(item.get("evidence_id")) for item in plan.get("selected_evidence") or [] if item.get("evidence_id") not in role_ids]
    return [by_id[evidence_id] for evidence_id in ordered_ids if evidence_id in selected and evidence_id in by_id]


def _is_current(item: dict[str, Any]) -> bool:
    period = item.get("time_period") or {}
    end = str(period.get("end") or "")
    return bool(re.search(r"(?i)\b(?:present|current|ongoing)\b", end))


def _date_value(value: Any) -> int:
    text = str(value or "").strip()
    match = re.search(r"(?i)\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)?\s*((?:19|20)\d{2})\b", text)
    if not match:
        return 0
    month_text = text[:match.start(1)].strip()[:3].title()
    try:
        month = datetime.strptime(month_text, "%b").month if month_text else 12
    except ValueError:
        month = 12
    return int(match.group(1)) * 12 + month


def _display_period(items: list[dict[str, Any]]) -> str:
    for item in items:
        period = item.get("time_period") or {}
        start, end = str(period.get("start") or "").strip(), str(period.get("end") or "").strip()
        if start and end:
            return f"{start} - {end}"
        if start:
            return start
    return ""


def _role_marker(section: str) -> str:
    parts = [part.strip() for part in section.split(">") if part.strip()]
    return parts[-1] if parts else ""


def _employer_marker(section: str) -> str:
    parts = [part.strip() for part in section.split(">") if part.strip()]
    return parts[-2] if len(parts) >= 3 else ""


def _has_bullet_content(item: dict[str, Any]) -> bool:
    return any(str(item.get(field) or "").strip() for field in ("action", "responsibility", "task", "result", "detail"))


def build_resume_curation_plan(
    job_model: dict[str, Any],
    matches: dict[str, Any],
    ckb: list[dict[str, Any]],
    target_words: int = 650,
    max_evidence: int | None = None,
    application_decision: dict[str, Any] | None = None,
    outcome_learning: dict[str, Any] | None = None,
    generation_date=None,
) -> dict[str, Any]:
    evidence_by_id = {str(item.get("evidence_id")): item for item in ckb if item.get("evidence_id")}
    max_evidence = len(evidence_by_id) if max_evidence is None else max_evidence
    source_order = {evidence_id: index for index, evidence_id in enumerate(evidence_by_id)}
    criteria = {str(item.get("criteria_id")): item for item in job_model.get("criteria") or []}
    decisions = {str(item.get("criteria_id")): item for item in (application_decision or {}).get("requirements") or []}
    historical_preference = {
        str(item.get("evidence_id")): 1
        for item in (outcome_learning or {}).get("evidence_signals") or []
        if item.get("signal") == "positive" and item.get("effect") == "tie_break_only" and item.get("identity_match") is True
    } if (outcome_learning or {}).get("status") == "available" else {}
    support: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

    for match in matches.get("matches") or []:
        criterion_id = str(match.get("criteria_id"))
        decision = decisions.get(criterion_id, {})
        classification = decision.get("evidence_classification")
        if classification in {"confirmed_gap", "unverified_possible"}:
            continue
        framing = {"verified_match": "direct", "adjacent_match": "adjacent"}.get(classification)
        framing = framing or ("direct" if match.get("match_type") == "direct" else "adjacent" if match.get("match_type") == "inferred" else None)
        if not framing:
            continue
        importance = str(decision.get("importance") or criteria.get(criterion_id, {}).get("criteria_type") or "unknown")
        for evidence_id in map(str, match.get("matched_evidence") or []):
            if evidence_id in evidence_by_id:
                support[evidence_id].append((criterion_id, importance, framing))

    def priority(item: dict[str, Any]) -> tuple:
        evidence_id = str(item.get("evidence_id"))
        links = support[evidence_id]
        direct_essential = {criterion_id for criterion_id, importance, framing in links if importance == "essential" and framing == "direct"}
        direct_desirable = {criterion_id for criterion_id, importance, framing in links if importance == "desirable" and framing == "direct"}
        adjacent_essential = {criterion_id for criterion_id, importance, framing in links if importance == "essential" and framing == "adjacent"}
        return (
            bool(direct_essential), len(direct_essential), bool(direct_desirable), bool(adjacent_essential),
            bool(str(item.get("result") or "").strip()), {"high": 2, "medium": 1}.get(str(item.get("evidence_quality")), 0),
            _is_current(item), historical_preference.get(evidence_id, 0), -source_order[evidence_id],
        )

    relevant = [item for item in evidence_by_id.values() if support[str(item.get("evidence_id"))]]
    fallback_credentials = [item for item in evidence_by_id.values() if not support[str(item.get("evidence_id"))] and item.get("evidence_type") in {"education", "qualification", "award", "skill"}]
    selected_ids: list[str] = []
    seen_content: set[str] = set()

    def select(item: dict[str, Any]) -> bool:
        evidence_id = str(item.get("evidence_id"))
        content_key = re.sub(r"\s+", " ", str(item.get("source_text") or "").strip().lower())
        if evidence_id in selected_ids or (content_key and content_key in seen_content):
            return False
        selected_ids.append(evidence_id)
        if content_key:
            seen_content.add(content_key)
        return True

    # Cover each verified essential requirement before adding depth to one requirement.
    for criterion_id in criteria:
        if len(selected_ids) >= max_evidence:
            break
        options = [item for item in relevant if any(link == (criterion_id, "essential", "direct") for link in support[str(item.get("evidence_id"))])]
        if any(any(link == (criterion_id, "essential", "direct") for link in support[evidence_id]) for evidence_id in selected_ids):
            continue
        for item in sorted(options, key=priority, reverse=True):
            if select(item):
                break

    for item in sorted(relevant, key=priority, reverse=True):
        if len(selected_ids) >= max_evidence:
            break
        select(item)

    for item in fallback_credentials:
        if len(selected_ids) >= max_evidence:
            break
        select(item)

    selected_set = set(selected_ids)
    role_supported_requirements: dict[str, set[str]] = defaultdict(set)
    role_has_essential: dict[str, bool] = defaultdict(bool)
    for evidence_id, item in evidence_by_id.items():
        section = str(item.get("source_section") or "Master Resume")
        for criterion_id, importance, framing in support[evidence_id]:
            role_supported_requirements[section].add(criterion_id)
            if framing == "direct":
                role_has_essential[section] |= importance == "essential"
    strength_promoted_sections = {
        section for section, _requirements in sorted(
            (
                (section, requirements) for section, requirements in role_supported_requirements.items()
                if len(requirements) >= 3 and not role_has_essential[section]
            ),
            key=lambda item: (-len(item[1]), item[0]),
        )[:2]
    }
    selected = []
    for evidence_id, item in evidence_by_id.items():
        if evidence_id not in selected_set:
            continue
        links = support[evidence_id]
        framing = "direct" if any(link[2] == "direct" for link in links) else "adjacent" if links else "continuity_only"
        selected.append({
            "evidence_id": evidence_id,
            "evidence_type": str(item.get("evidence_type") or "experience"),
            "source_section": str(item.get("source_section") or "Master Resume"),
            "supports_requirements": sorted({link[0] for link in links}),
            "curation_action": "feature" if (
                any(link[1] == "essential" and link[2] == "direct" for link in links)
                or str(item.get("source_section") or "Master Resume") in strength_promoted_sections
            ) else "include_concisely",
            "evidence_framing": framing,
            "fact_policy": "preserve_source_facts_only",
            **(evidence_density(item) if item.get("evidence_type") == "experience" else {}),
        })

    role_groups: dict[str, list[dict[str, Any]]] = {}
    for item in evidence_by_id.values():
        if item.get("evidence_type") == "experience":
            role_groups.setdefault(str(item.get("source_section") or "Master Resume"), []).append(item)
    roles = []
    for source_order, (section, items) in enumerate(role_groups.items()):
        role_selected = [str(item.get("evidence_id")) for item in items if str(item.get("evidence_id")) in selected_set]
        links = [link for item in items for link in support[str(item.get("evidence_id"))] if str(item.get("evidence_id")) in selected_set]
        is_current = any(_is_current(item) for item in items)
        direct_essential = any(importance == "essential" and framing == "direct" for _, importance, framing in links)
        if direct_essential or section in strength_promoted_sections:
            action, cap = "promote", 4
        elif links:
            action, cap = "keep", 2
        elif is_current:
            action, cap = "compress", 1
        else:
            action, cap = "omit", 0
        framing = "direct" if any(link[2] == "direct" for link in links) else "adjacent" if links else "continuity_only" if is_current else None
        roles.append({
            "source_section": section,
            "chronology_order": source_order,
            "source_order": source_order,
            "display_period": _display_period(items),
            "date_status": "verified" if _display_period(items) else "uncertain" if any(item.get("time_period_status") == "uncertain" for item in items) else "not_provided",
            "employer_marker": _employer_marker(section),
            "role_marker": _role_marker(section),
            "is_current": is_current,
            "curation_action": action,
            "include_role_header": action != "omit",
            "selected_evidence_ids": role_selected,
            "max_bullets": None if role_selected else 0,
            "supports_requirements": sorted({link[0] for link in links}),
            "evidence_framing": framing,
            "rationale": {
                "promote": (
                    "Verified direct support for an essential requirement."
                    if direct_essential else
                    "Grounded direct or transferable support across at least three job requirements."
                ),
                "keep": "Grounded relevant or transferable evidence.",
                "compress": "Retained for explicit current-role continuity only.",
                "omit": "No selected job-relevant evidence or continuity need.",
            }[action],
        })
    roles.sort(key=lambda role: (
        not role["is_current"],
        -max((_date_value((item.get("time_period") or {}).get("end")) for item in role_groups[role["source_section"]]), default=0),
        -max((_date_value((item.get("time_period") or {}).get("start")) for item in role_groups[role["source_section"]]), default=0),
        role["source_order"],
    ))
    for chronology_order, role in enumerate(roles):
        role["chronology_order"] = chronology_order

    timeline = apply_timeline(roles, role_groups, generation_date)
    ineligible = {section for section in role_groups if next(role for role in roles if role["source_section"] == section)["display_mode"] in {"timeline_only", "hidden"}}
    selected = [item for item in selected if item["source_section"] not in ineligible]
    selected_set = {item["evidence_id"] for item in selected}
    return {
        "schema_version": RESUME_PLAN_SCHEMA_VERSION,
        "timeline": timeline,
        "target_words": target_words,
        "target_pages": 2,
        "maximum_pages": None,
        "required_sections": ["Professional Summary", "Key Skills", "Work Experience"],
        "roles": roles,
        "selected_evidence": selected,
        "omitted_evidence_ids": sorted(set(evidence_by_id) - selected_set),
        "omission_reasons": {evidence_id: (
            "duplicate" if re.sub(r"\s+", " ", str(item.get("source_text") or "").strip().lower()) in seen_content
            else "explicit_evidence_budget" if support[evidence_id] else "low_relevance"
        ) for evidence_id, item in evidence_by_id.items() if evidence_id not in selected_set},
        "section_budget": {"professional_summary": 80, "key_skills": 90, "work_experience": max(target_words - 220, 250), "education_qualifications_references": 50},
        "rules": [
            "Preserve role chronology from authoritative CKB source order; relevance controls only content budget.",
            "max_bullets null means no mechanical ceiling. One evidence record may support multiple distinct actions; never repeat an action to fill space.",
            "Word and page targets are guidance; employer limits take priority. Preserve distinctive relevant facts before compressing generic duties.",
            "Adjacent and continuity-only evidence must not be presented as direct JD capability evidence.",
            "promote, keep and compress roles must retain their role header; omit roles may be absent.",
            "Do not create achievements, metrics, titles, employers or dates.",
        ],
    }
