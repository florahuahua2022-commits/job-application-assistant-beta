import json


DOCUMENT_KEYS = {
    "resume": "tailored_resume",
    "cover_letter": "cover_letter",
    "selection_criteria": "selection_criteria",
}


def required_generated_documents(requirements: dict) -> tuple[str, ...]:
    """Return only explicitly required standalone generated documents."""
    documents = requirements.get("documents") or {}
    return tuple(
        generated
        for requirement_name, generated in DOCUMENT_KEYS.items()
        if (documents.get(requirement_name) or {}).get("requirement") == "required"
        and (documents.get(requirement_name) or {}).get("format") == "standalone"
    )


def standalone_selection_criteria_required(requirements: dict) -> bool:
    selection = (requirements.get("documents") or {}).get("selection_criteria") or {}
    return selection.get("requirement") == "required" and selection.get("format") == "standalone"


def selection_criteria_context_required(requirements: dict) -> bool:
    selection = (requirements.get("documents") or {}).get("selection_criteria") or {}
    return standalone_selection_criteria_required(requirements) or selection.get("format") in {
        "embedded_in_cover_letter", "embedded_in_resume",
    }


def persist_selection_contract(bundle: dict, selection_plan: dict, evidence_allocation: dict) -> dict:
    """Attach the exact generation-time contracts without mutating either input plan."""
    return {**bundle, "selection_plan": selection_plan, "evidence_allocation": evidence_allocation}


def document_evidence_issues(document_type: str, structured_json: str, used_json: str) -> list[dict]:
    try:
        structured = json.loads(structured_json or "{}")
        used = {str(value) for value in json.loads(used_json or "[]")}
    except (json.JSONDecodeError, TypeError):
        return [{"code": "document_evidence_metadata_invalid", "message": "The document evidence metadata is invalid."}]

    if document_type in {"tailored_resume", "cover_letter"}:
        allowed = {
            str(item.get("evidence_id"))
            for item in structured.get("selected_evidence") or []
            if item.get("evidence_id") is not None
        }
        invalid = sorted(used - allowed)
        return [{
            "code": f"{document_type}_unselected_evidence",
            "message": f"The document declares evidence outside its plan: {', '.join(invalid)}.",
        }] if invalid else []

    if document_type != "selection_criteria":
        return []
    plan = structured.get("selection_plan") or {}
    allowed_by_criterion = {
        str(item.get("criteria_id")): {str(value) for value in item.get("matched_evidence") or []}
        for item in plan.get("items") or []
    }
    issues = []
    declared = set()
    for response in structured.get("responses") or []:
        criterion_id = str(response.get("criteria_id") or "")
        response_used = {str(value) for value in response.get("evidence_used") or []}
        declared.update(response_used)
        invalid = sorted(response_used - allowed_by_criterion.get(criterion_id, set()))
        if invalid:
            issues.append({
                "code": "selection_criteria_unselected_evidence",
                "message": f"Criterion {criterion_id} declares evidence outside its matched allow-list: {', '.join(invalid)}.",
            })
    invalid_metadata = sorted(used - set().union(*allowed_by_criterion.values()) if allowed_by_criterion else used)
    if invalid_metadata or used != declared:
        issues.append({
            "code": "selection_criteria_evidence_metadata_mismatch",
            "message": "Selection Criteria evidence metadata does not match the applied plan and final responses.",
        })
    return issues


def build_pack_review_payload(
    documents: dict[str, dict], ckb: list[dict], decision: dict, identity: dict,
) -> dict | None:
    """Build only bounded cross-document candidates; never infer prose-to-evidence attribution."""
    uses: dict[str, set[str]] = {}
    parsed: dict[str, dict] = {}
    for document_type, document in documents.items():
        structured = document.get("structured") or {}
        used = {str(value) for value in document.get("used_evidence_ids") or []}
        parsed[document_type] = {**document, "structured": structured, "used_evidence_ids": sorted(used)}
        for evidence_id in used:
            uses.setdefault(evidence_id, set()).add(document_type)
    shared_ids = {evidence_id for evidence_id, document_types in uses.items() if len(document_types) >= 2}

    selection = parsed.get("selection_criteria", {}).get("structured", {})
    selection_responses = selection.get("responses") or []
    cover = parsed.get("cover_letter", {}).get("structured", {})
    decision_by_id = {
        str(item.get("criteria_id")): item for item in decision.get("requirements") or []
        if item.get("evidence_classification") in {"confirmed_gap", "unverified_possible", "adjacent_match"}
    }
    cover_gaps = {str(value) for value in cover.get("evidence_gaps") or []}
    selection_ids = {str(item.get("criteria_id")) for item in selection_responses}
    gap_ids = set(decision_by_id) & cover_gaps & selection_ids
    if not shared_ids and not gap_ids:
        return None

    evidence_by_id = {str(item.get("evidence_id")): item for item in ckb if isinstance(item, dict)}
    candidate_ids = sorted(shared_ids & set(evidence_by_id))
    if not candidate_ids and not gap_ids:
        return None
    allocation_items = {
        str(item.get("evidence_id")): item for item in selection.get("evidence_allocation", {}).get("items") or []
    }
    resume = parsed.get("tailored_resume", {}).get("structured", {})
    groups = []
    for evidence_id in candidate_ids:
        resume_item = next((item for item in resume.get("selected_evidence") or [] if str(item.get("evidence_id")) == evidence_id), {})
        allocation = allocation_items.get(evidence_id, {})
        group_uses = []
        for document_type in sorted(uses[evidence_id]):
            use = {"document_type": document_type, "passage_attribution": "not_structured"}
            if document_type == "tailored_resume":
                use.update((allocation.get("resume") or {}))
            elif document_type == "cover_letter":
                selected = next((item for item in cover.get("selected_evidence") or [] if str(item.get("evidence_id")) == evidence_id), {})
                use.update({key: selected.get(key) for key in ("allocation_use", "purpose") if selected.get(key) is not None})
            else:
                criteria = [
                    {
                        "criteria_id": str(response.get("criteria_id")),
                        "text": str(response.get("final_response") or ""),
                        "purpose": next((
                            str(value.get("purpose"))
                            for plan_item in selection.get("selection_plan", {}).get("items") or []
                            if str(plan_item.get("criteria_id")) == str(response.get("criteria_id"))
                            for value in plan_item.get("evidence_allocation") or []
                            if str(value.get("evidence_id")) == evidence_id
                        ), "unspecified"),
                    }
                    for response in selection_responses if evidence_id in map(str, response.get("evidence_used") or [])
                ]
                use.update({"criteria": criteria, "passage_attribution": "criterion_structured"})
            group_uses.append(use)
        groups.append({
            "evidence_id": evidence_id,
            "source": evidence_by_id[evidence_id],
            "framing": allocation.get("framing") or resume_item.get("evidence_framing") or "direct",
            "uses": group_uses,
            "allocation": allocation,
        })

    relevant_documents: dict[str, dict] = {}
    for document_type, document in parsed.items():
        if not (set(document["used_evidence_ids"]) & shared_ids) and document_type not in {
            "cover_letter" if gap_ids else "", "selection_criteria" if gap_ids else "",
        }:
            continue
        if document_type == "selection_criteria":
            plan_by_id = {
                str(item.get("criteria_id")): item for item in selection.get("selection_plan", {}).get("items") or []
            }
            responses = [
                item for item in selection_responses
                if set(map(str, item.get("evidence_used") or [])) & shared_ids or str(item.get("criteria_id")) in gap_ids
            ]
            criteria_ids = {str(item.get("criteria_id")) for item in responses}
            relevant_documents[document_type] = {
                "responses": responses,
                "plan_items": [plan_by_id[value] for value in criteria_ids if value in plan_by_id],
            }
        elif document_type == "cover_letter":
            relevant_documents[document_type] = {
                "content": document.get("content", ""),
                "used_evidence_ids": sorted(set(document["used_evidence_ids"]) & shared_ids),
                "selected_evidence": [
                    item for item in cover.get("selected_evidence") or []
                    if str(item.get("evidence_id")) in shared_ids
                ],
                "evidence_gaps": sorted(gap_ids),
            }
        else:
            relevant_documents[document_type] = {
                "content": document.get("content", ""),
                "used_evidence_ids": sorted(set(document["used_evidence_ids"]) & shared_ids),
                "selected_evidence": [
                    item for item in resume.get("selected_evidence") or []
                    if str(item.get("evidence_id")) in shared_ids
                ],
            }
    return {
        "schema_version": "1.0",
        "identity": identity,
        "documents": relevant_documents,
        "evidence_groups": groups,
        "gap_candidates": [
            {**decision_by_id[value], "comparison_document_types": ["cover_letter", "selection_criteria"]}
            for value in sorted(gap_ids)
        ],
    }
