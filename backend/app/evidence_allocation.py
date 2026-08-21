from copy import deepcopy
from typing import Any


EVIDENCE_ALLOCATION_SCHEMA_VERSION = "1.0"


def _strength(item: dict[str, Any]) -> tuple[int, int]:
    return (bool(str(item.get("result") or "").strip()), {"high": 2, "medium": 1}.get(str(item.get("evidence_quality")), 0))


def build_evidence_allocation(
    resume_plan: dict[str, Any],
    selection_plan: dict[str, Any],
    ckb: list[dict[str, Any]],
    application_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_by_id = {str(item.get("evidence_id")): item for item in ckb if item.get("evidence_id")}
    resume_by_id = {str(item.get("evidence_id")): item for item in resume_plan.get("selected_evidence") or []}
    decisions = {str(item.get("criteria_id")): item for item in (application_decision or {}).get("requirements") or []}
    items: dict[str, dict[str, Any]] = {}

    def allocation(evidence_id: str) -> dict[str, Any]:
        return items.setdefault(evidence_id, {"evidence_id": evidence_id, "selection_criteria": []})

    for evidence_id, resume_item in resume_by_id.items():
        framing = str(resume_item.get("evidence_framing") or "direct")
        use, purpose = (
            ("allowed_if_needed", "continuity") if framing == "continuity_only" else
            ("secondary", "bridge") if framing == "adjacent" else
            ("primary", "breadth") if resume_item.get("curation_action") == "feature" else
            ("secondary", "breadth")
        )
        allocation(evidence_id)["resume"] = {"use": use, "purpose": purpose}

    primary_ids: set[str] = set()
    comparable_alternatives: set[str] = set()
    framing_by_id: dict[str, str] = {}
    cover_strength: dict[str, tuple[int, int, int, int, int]] = {}
    selection_items = selection_plan.get("items") or []
    cover_only = not selection_items
    allocation_items = selection_items or [{
        "criteria_id": item.get("criteria_id"),
        "matched_evidence": item.get("matched_evidence") or [],
        "match_type": "direct" if item.get("evidence_classification") == "verified_match" else "inferred",
        "coverage": "strong" if item.get("evidence_classification") == "verified_match" else "partial",
        "criteria_type": item.get("importance"),
    } for item in (application_decision or {}).get("requirements") or [] if item.get("evidence_classification") in {"verified_match", "adjacent_match"}]
    for plan_item in allocation_items:
        criteria_id = str(plan_item.get("criteria_id"))
        if decisions.get(criteria_id, {}).get("evidence_classification") in {"confirmed_gap", "unverified_possible"}:
            continue
        matched = [str(value) for value in plan_item.get("matched_evidence") or [] if str(value) in evidence_by_id]
        if not matched:
            continue
        strongest = max(_strength(evidence_by_id[evidence_id]) for evidence_id in matched)
        comparable = [evidence_id for evidence_id in matched if _strength(evidence_by_id[evidence_id]) == strongest]
        primary = next((evidence_id for evidence_id in comparable if evidence_id not in primary_ids), comparable[0])
        primary_ids.add(primary)
        framing = "direct" if plan_item.get("match_type") == "direct" else "adjacent"
        for evidence_id in matched:
            use = "primary" if evidence_id == primary else "secondary" if evidence_id in comparable else "allowed_if_needed"
            route = {
                "criteria_id": criteria_id, "use": use,
                "purpose": "criterion_depth" if framing == "direct" else "bridge",
            }
            allocation(evidence_id).setdefault("cover_requirements" if cover_only else "selection_criteria", []).append(route)
            if framing == "direct" or evidence_id not in framing_by_id:
                framing_by_id[evidence_id] = framing
            cover_strength[evidence_id] = max(cover_strength.get(evidence_id, (0, 0, 0, 0, 0)), (
                2 if framing == "direct" else 1,
                {"strong": 2, "partial": 1}.get(str(plan_item.get("coverage")), 0),
                1 if plan_item.get("criteria_type") == "essential" else 0,
                *_strength(evidence_by_id[evidence_id]),
            ))
            if evidence_id != primary and evidence_id in comparable:
                comparable_alternatives.add(evidence_id)

    strongest_cover = max(cover_strength.values(), default=(0, 0, 0, 0, 0))
    for evidence_id in {value for item in allocation_items for value in map(str, item.get("matched_evidence") or [])}:
        if evidence_id not in evidence_by_id or evidence_id not in items:
            continue
        item = allocation(evidence_id)
        framing = framing_by_id.get(evidence_id, "direct")
        if cover_only and framing == "direct" and cover_strength.get(evidence_id) == strongest_cover:
            use, purpose = "primary", "differentiator"
        elif framing == "adjacent" and cover_strength.get(evidence_id) == strongest_cover:
            use, purpose = "allowed_if_needed", "bridge"
        elif framing == "adjacent":
            use, purpose = "avoid", "bridge"
        elif evidence_id in comparable_alternatives and cover_strength.get(evidence_id) == strongest_cover and evidence_id not in resume_by_id:
            use, purpose = "primary", "differentiator"
        elif evidence_id in comparable_alternatives and cover_strength.get(evidence_id) == strongest_cover:
            use, purpose = "secondary", "differentiator"
        elif evidence_id in primary_ids and cover_strength.get(evidence_id) == strongest_cover:
            use, purpose = "allowed_if_needed", "differentiator"
        else:
            use, purpose = "avoid", "differentiator"
        item["cover_letter"] = {"use": use, "purpose": purpose}
        item["framing"] = framing

    return {"schema_version": EVIDENCE_ALLOCATION_SCHEMA_VERSION, "items": list(items.values())}


def apply_selection_allocation(selection_plan: dict[str, Any], allocation_plan: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(selection_plan)
    by_criterion: dict[str, list[dict[str, str]]] = {}
    for item in allocation_plan.get("items") or []:
        for assignment in item.get("selection_criteria") or []:
            by_criterion.setdefault(str(assignment.get("criteria_id")), []).append({
                "evidence_id": str(item.get("evidence_id")),
                "use": str(assignment.get("use")),
                "purpose": str(assignment.get("purpose")),
            })
    for item in result.get("items") or []:
        item["evidence_allocation"] = by_criterion.get(str(item.get("criteria_id")), [])
    result["evidence_allocation_schema_version"] = allocation_plan.get("schema_version")
    return result
