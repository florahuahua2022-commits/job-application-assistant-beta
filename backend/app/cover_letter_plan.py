import json
from calendar import monthrange
from datetime import date, datetime
import re
from typing import Any


COVER_LETTER_PLAN_SCHEMA_VERSION = "1.0"

COVER_LETTER_FACT_RULES = (
    "Every applicant employment claim, including opening summaries, must be supported by plan-selected evidence; "
    "candidate_evidence_ids and unselected records are not permission to use them. Check actual prose, not only declared IDs. "
    "Use past tense for completed employment. Never say current role, currently employed, or use present-tense duties "
    "for a closed past employment period. Current employment requires explicit current/present/ongoing status or an "
    "open-ended authoritative time_period; missing dates do not establish current employment. "
    "Never infer government policies, procedures or governance expertise merely from a government employer."
)


def _employment_end(value: str) -> date | None:
    # Month/year precision means the end of that month/year, not its first day.
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%b %Y", "%B %Y", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
        if fmt == "%Y":
            return parsed.replace(month=12, day=31)
        if "%d" not in fmt:
            return parsed.replace(day=monthrange(parsed.year, parsed.month)[1])
        return parsed
    return None


def _present_predicate(sentence: str) -> bool:
    # ponytail: first-person verb-form heuristic, not a grammatical parser. Unusual
    # subjects/subordinate clauses can be missed or misclassified; use a parser if
    # reviewed examples justify it. Perfect/past/modal clauses are not current duties.
    past_or_modal = set("was were had did led ran wrote made took gave held kept met built dealt felt found got knew left read saw sent set spoke spent stood taught thought understood won would could should might may must can will shall".split())
    text = sentence.casefold()
    for match in re.finditer(r"\b(?:i|we)\s+(?:(?:[a-z]+ly|also|still|often|now|never|always)\s+)*([a-z]+)\b", text):
        verb = match.group(1)
        if verb in past_or_modal or verb.endswith("ed") or verb in {"have", "has"}:
            continue
        return True
    return False


def cover_letter_contract_issues(content: str, ckb: list[dict], plan: dict, as_of: date | None = None) -> list[dict]:
    as_of = as_of or date.today()
    selected_ids = selected_cover_letter_evidence_ids(plan)

    def normalise(value: str) -> str:
        return re.sub(r"[^\w]+", " ", value.casefold()).strip()

    employers: dict[str, list[dict]] = {}
    for item in ckb:
        parts = str(item.get("source_section") or "").split(">")
        name = str(item.get("organization") or (parts[-2].split("|")[0] if len(parts) >= 3 else "")).strip()
        if normalise(name):
            employers.setdefault(normalise(name), []).append(item)
    selected_employers = {name for name, items in employers.items()
                          if any(str(item.get("evidence_id")) in selected_ids for item in items)}
    # Longest full name wins: 'ABC Services' must not also match employer 'ABC'.
    names = sorted(employers, key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)") if names else None
    body = re.sub(r"<!--.*?-->", "", content, flags=re.S)
    greeting = re.search(r"(?im)^\s*Dear[^\n]*\n", body)
    if greeting:
        body = body[greeting.end():]
    body = re.split(r"(?im)^\s*(?:Yours (?:sincerely|faithfully)|Kind regards|Best regards)\b", body)[0]
    issues = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n\s*\n", body):
        text = normalise(sentence)
        mentioned_names = {match.group() for match in pattern.finditer(text)} if pattern else set()
        excluded = mentioned_names - selected_employers
        # Body context mentions are deliberately subject to the same whitelist;
        # no employment-phrase or first-person classification can bypass this check.
        if excluded:
            issues.append({"type": "unmatched_evidence_used", "description": "Cover letter body names employers outside the selected evidence.",
                           "evidence": ", ".join(sorted(excluded)), "location": sentence,
                           "recommended_action": "Remove unselected employer mentions; do not expand the approved plan."})
        mentioned = [item for name in mentioned_names for item in employers[name]]
        explicit_current = bool(re.search(r"\bmy current (?:role|position|job)\b", text))
        # Remove employer adjuncts so 'I, at X, manage ...' exposes the same verb
        # as 'At X, I manage ...', without enumerating employment sentences.
        predicate = normalise(re.sub(r"(?i)\b(I|we)['’](ve|re|m|d|ll)\b",
                                    lambda m: m[1] + " " + {"ve": "have", "re": "are", "m": "am", "d": "would", "ll": "will"}[m[2].lower()], sentence))
        if pattern:
            predicate = pattern.sub(" EMPLOYER ", predicate)
        predicate = re.sub(r"\b(?:at|with|for|within|from)\s+EMPLOYER\b", " ", predicate)
        predicate = re.sub(r"\s+", " ", predicate)
        present = _present_predicate(predicate)
        candidates = mentioned or ([item for item in ckb if str(item.get("evidence_id")) in selected_ids] if explicit_current else [])
        ends = [str((item.get("time_period") or {}).get("end") or "").strip() for item in candidates]
        ended = [end for end in ends if (parsed := _employment_end(end)) is not None and parsed < as_of]
        unestablished = explicit_current and (not ends or any(not re.fullmatch(r"(?i)present|current|ongoing|now", end) and _employment_end(end) is None for end in ends))
        if (ended and (present or explicit_current)) or unestablished:
            issues.append({"type": "contradiction", "description": "Present employment wording conflicts with an ended or unestablished employment period.",
                           "location": sentence, "evidence": json.dumps([item.get("time_period") for item in candidates]),
                           "recommended_action": "Use past tense or remove the unsupported current-employment claim."})
    return issues


def selected_cover_letter_evidence_ids(plan: dict[str, Any]) -> set[str]:
    return {str(item.get("evidence_id")) for item in plan.get("selected_evidence") or [] if item.get("evidence_id")}


def cover_letter_evidence_pack(ckb_json: str, plan_json: str) -> list[dict[str, Any]]:
    try:
        ckb, plan = json.loads(ckb_json or "[]"), json.loads(plan_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return []
    by_id = {str(item.get("evidence_id")): item for item in ckb if isinstance(item, dict)}
    return [by_id[str(item.get("evidence_id"))] for item in plan.get("selected_evidence") or [] if str(item.get("evidence_id")) in by_id]


def build_cover_letter_plan(
    job_model: dict[str, Any],
    matches: dict[str, Any],
    ckb: list[dict[str, Any]],
    applicant_profile: Any | None = None,
    evidence_already_detailed: list[str] | None = None,
    evidence_allocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    matches_by_id = {str(item.get("criteria_id")): item for item in matches.get("matches") or []}
    evidence_by_id = {str(item.get("evidence_id")): item for item in ckb}
    detailed = set(evidence_already_detailed or [])
    candidates = []
    for index, criterion in enumerate(job_model.get("criteria") or []):
        criteria_id = str(criterion.get("criteria_id"))
        match = matches_by_id.get(criteria_id, {})
        score = {"direct": 30, "inferred": 15, "insufficient": 0}.get(str(match.get("match_type")), 0)
        score += {"strong": 10, "partial": 5, "weak": 0}.get(str(match.get("coverage")), 0)
        if criterion.get("criteria_type") == "essential":
            score += 8
        candidates.append((score, -index, criterion, match))
    candidates.sort(reverse=True, key=lambda item: (item[0], item[1]))

    priorities = []
    evidence_candidates: list[str] = []
    for _score, _order, criterion, match in candidates[:3]:
        matched_ids = [str(value) for value in match.get("matched_evidence") or [] if str(value) in evidence_by_id]
        priorities.append({
            "criteria_id": str(criterion.get("criteria_id")),
            "requirement": str(criterion.get("criteria_text") or ""),
            "criteria_type": str(criterion.get("criteria_type") or "inferred"),
            "match_type": str(match.get("match_type") or "insufficient"),
            "coverage": str(match.get("coverage") or "weak"),
            "candidate_evidence_ids": matched_ids,
        })
        evidence_candidates.extend(value for value in matched_ids if value not in evidence_candidates)

    allocation_by_id = {str(item.get("evidence_id")): item for item in (evidence_allocation or {}).get("items") or []}
    if allocation_by_id:
        order = {"primary": 0, "secondary": 1, "allowed_if_needed": 2, "avoid": 3}
        eligible = [
            value for value in evidence_candidates
            if allocation_by_id.get(value, {}).get("cover_letter")
            and allocation_by_id[value]["cover_letter"].get("use") != "avoid"
        ]
        ranked = sorted(eligible, key=lambda value: order.get(allocation_by_id[value]["cover_letter"].get("use"), 3))
        primary = [value for value in ranked if allocation_by_id[value]["cover_letter"].get("use") == "primary"]
        selected_ids = primary[:2] if primary else ranked[:1]
        if len(selected_ids) == 1 and primary:
            def routes(evidence_id: str) -> list[dict[str, Any]]:
                item = allocation_by_id[evidence_id]
                return item.get("selection_criteria") or item.get("cover_requirements") or []

            covered = {
                str(item.get("criteria_id"))
                for item in routes(selected_ids[0])
            }
            bridge = next((
                value for value in ranked
                if value not in selected_ids
                and allocation_by_id[value]["cover_letter"].get("purpose") == "bridge"
                and any(str(item.get("criteria_id")) not in covered for item in routes(value))
            ), None)
            if bridge:
                selected_ids.append(bridge)
    else:
        fresh = [value for value in evidence_candidates if value not in detailed]
        repeated = [value for value in evidence_candidates if value in detailed]
        selected_ids = (fresh + repeated)[:2]
    selected_evidence = [{
        "evidence_id": evidence_id,
        "source_section": str(evidence_by_id[evidence_id].get("source_section") or "Master Resume"),
        "previously_detailed": evidence_id in detailed or any(
            item.get("use") == "primary" for item in allocation_by_id.get(evidence_id, {}).get("selection_criteria") or []
        ),
        "allocation_use": allocation_by_id.get(evidence_id, {}).get("cover_letter", {}).get("use", "primary"),
        "purpose": allocation_by_id.get(evidence_id, {}).get("cover_letter", {}).get("purpose", "differentiator"),
    } for evidence_id in selected_ids]
    intent = {
        "source": "user_declared_intent_not_career_evidence",
        "target_direction": getattr(applicant_profile, "target_direction", None) or "",
        "motivation": getattr(applicant_profile, "motivation", None) or "",
        "writing_tone": getattr(applicant_profile, "writing_tone", "natural_professional") or "natural_professional",
        "preferences_notes": getattr(applicant_profile, "preferences_notes", None) or "",
    }
    has_declared_motivation = bool(intent["motivation"].strip() and intent["motivation"].strip().lower() != "not provided")
    return {
        "schema_version": COVER_LETTER_PLAN_SCHEMA_VERSION,
        "priorities": priorities,
        "selected_evidence": selected_evidence,
        "evidence_gaps": [item["criteria_id"] for item in priorities if item["match_type"] == "insufficient"],
        "declared_intent": intent,
        "narrative_plan": [
            {"section": "opening", "purpose": "Name the role and organisation without generic enthusiasm claims.", "target_share": 0.15},
            {"section": "role_and_organisation_alignment", "purpose": (
                "Use declared motivation and JD context; do not present intent as employment fact."
                if has_declared_motivation else
                "Use neutral advertised-role and organisation facts only; do not invent applicant motivation, values or purpose."
            ), "target_share": 0.45},
            {"section": "evidence", "purpose": "Use at most two selected evidence items and avoid retelling the resume.", "target_share": 0.30},
            {"section": "close", "purpose": "Close naturally and confirm only supported requirements.", "target_share": 0.10},
        ],
    }
