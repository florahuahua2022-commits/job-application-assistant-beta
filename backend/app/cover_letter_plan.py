from typing import Any


COVER_LETTER_PLAN_SCHEMA_VERSION = "1.0"


def selected_cover_letter_evidence_ids(plan: dict[str, Any]) -> set[str]:
    return {
        str(item.get("evidence_id"))
        for item in plan.get("selected_evidence") or []
        if item.get("evidence_id")
    }


def build_cover_letter_plan(
    job_model: dict[str, Any],
    matches: dict[str, Any],
    ckb: list[dict[str, Any]],
    applicant_profile: Any | None = None,
    evidence_already_detailed: list[str] | None = None,
) -> dict[str, Any]:
    matches_by_id = {str(item.get("criteria_id")): item for item in matches.get("matches") or []}
    evidence_by_id = {str(item.get("evidence_id")): item for item in ckb}
    detailed = set(evidence_already_detailed or [])
    candidates = []
    for index, criterion in enumerate(job_model.get("criteria") or []):
        criteria_id = str(criterion.get("criteria_id"))
        match = matches_by_id.get(criteria_id, {})
        score = {
            "direct": 30, "inferred": 15, "insufficient": 0,
        }.get(str(match.get("match_type")), 0)
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

    fresh = [value for value in evidence_candidates if value not in detailed]
    repeated = [value for value in evidence_candidates if value in detailed]
    selected_ids = (fresh + repeated)[:2]
    selected_evidence = [{
        "evidence_id": evidence_id,
        "source_section": str(evidence_by_id[evidence_id].get("source_section") or "Master Resume"),
        "previously_detailed": evidence_id in detailed,
    } for evidence_id in selected_ids]
    intent = {
        "source": "user_declared_intent_not_career_evidence",
        "target_direction": getattr(applicant_profile, "target_direction", None) or "",
        "motivation": (
            getattr(applicant_profile, "motivation", None) or ""
            if getattr(applicant_profile, "motivation_confirmed", False)
            else ""
        ),
        "writing_tone": getattr(applicant_profile, "writing_tone", "natural_professional") or "natural_professional",
        "preferences_notes": getattr(applicant_profile, "preferences_notes", None) or "",
    }
    return {
        "schema_version": COVER_LETTER_PLAN_SCHEMA_VERSION,
        "priorities": priorities,
        "selected_evidence": selected_evidence,
        "evidence_gaps": [item["criteria_id"] for item in priorities if item["match_type"] == "insufficient"],
        "declared_intent": intent,
        "narrative_plan": [
            {"section": "opening", "purpose": "Name the role and organisation without generic enthusiasm claims.", "target_share": 0.15},
            {"section": "role_and_organisation_alignment", "purpose": "Use declared motivation and JD context; do not present intent as employment fact.", "target_share": 0.45},
            {"section": "evidence", "purpose": "Use at most two selected evidence items and avoid retelling the resume.", "target_share": 0.30},
            {"section": "close", "purpose": "Close naturally and confirm only supported requirements.", "target_share": 0.10},
        ],
    }
