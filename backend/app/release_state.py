from hashlib import sha256
import json
from typing import Any


def load_release_state(value: str | None) -> dict[str, Any]:
    try:
        state = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        state = {}
    return state if isinstance(state, dict) else {}


def fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def generation_inputs_fingerprint(application: Any, resume: Any, profile: Any | None) -> str:
    return fingerprint({
        "job_model": application.job_model_json,
        "application_requirements": application.application_requirements_json,
        "application_decision": application.application_decision_json,
        "ckb": resume.ckb_json,
        "source_text": getattr(resume, "source_text", ""),
        "resume_snapshot": getattr(application, "resume_snapshot_json", "{}"),
        "profile": {
            key: getattr(profile, key, None)
            for key in ("title", "first_name", "last_name", "preferred_name", "phone", "email", "postal_address", "suburb", "state", "postcode", "country", "work_rights", "availability_notice", "target_direction", "motivation", "writing_tone", "preferences_notes")
        },
    })


def document_is_current(document: Any, input_fingerprint: str, require_contract: bool = False) -> bool:
    try:
        recorded = (json.loads(document.trace_json or "{}") or {}).get("input_fingerprint")
    except (json.JSONDecodeError, TypeError):
        recorded = None
    return recorded == input_fingerprint if recorded else not require_contract


def details_fingerprint(application: Any, profile: Any | None) -> str:
    return fingerprint({
        "company": application.company,
        "position_title": application.position_title,
        "job_url": application.job_url,
        "profile": {
            "id": getattr(profile, "id", None),
            "first_name": getattr(profile, "first_name", None),
            "last_name": getattr(profile, "last_name", None),
            "phone": getattr(profile, "phone", None),
            "email": getattr(profile, "email", None),
        },
    })


def document_identity(document: Any) -> dict[str, Any]:
    return {
        "document_id": document.id,
        "content_sha256": fingerprint(document.content),
        "reviewer_sha256": fingerprint(document.reviewer_json or "{}"),
        "plan_sha256": fingerprint(document.structured_content_json or "{}"),
    }


def pack_fingerprint(application: Any, profile: Any | None, documents: dict[str, Any]) -> str:
    return fingerprint({
        "application": {
            "company": application.company,
            "position_title": application.position_title,
            "job_url": application.job_url,
            "job_description": application.job_description,
            "selection_criteria": application.selection_criteria,
            "job_model_json": application.job_model_json,
            "application_requirements_json": application.application_requirements_json,
            "application_decision_json": application.application_decision_json,
            "selection_plan_json": application.selection_plan_json,
            "selection_confirmations_json": application.selection_confirmations_json,
        },
        "profile": {
            "id": getattr(profile, "id", None),
            "first_name": getattr(profile, "first_name", None),
            "last_name": getattr(profile, "last_name", None),
            "phone": getattr(profile, "phone", None),
            "email": getattr(profile, "email", None),
        },
        "documents": {key: document_identity(value) for key, value in sorted(documents.items())},
    })


def pack_review_is_current(state: dict, current_fingerprint: str) -> bool:
    review = state.get("pack_review") or {}
    result = review.get("result") or {}
    return review.get("fingerprint") == current_fingerprint and result.get("status") == "pass" and not result.get("blocks_release")


def ats_is_current(state: dict, document: Any, format: str, template: str) -> bool:
    ats = state.get("ats") or {}
    result = ats.get("result") or {}
    return (
        ats.get("document_id") == document.id
        and ats.get("content_sha256") == fingerprint(document.content)
        and ats.get("format") == format
        and ats.get("template") == template
        and result.get("ready") is True
    )
