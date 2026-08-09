from datetime import datetime, timezone
import json
from typing import Any

from .applicant_profile import APPLICANT_PROFILE_SCHEMA_VERSION
from .ckb import CKB_SCHEMA_VERSION
from .government_writing_rules import GOVERNMENT_WRITING_RULES_VERSION
from .job_model import JOB_MODEL_SCHEMA_VERSION
from .reviewer import REVIEW_SCHEMA_VERSION
from .selection_logic import SELECTION_PLAN_SCHEMA_VERSION


GENERATION_TRACE_SCHEMA_VERSION = "1.0"
DOCUMENT_PROMPT_VERSION = "1.0"


def _json_value(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def build_trace_bundle(document: Any) -> dict[str, Any]:
    return {
        "bundle_schema_version": "1.0",
        "run_id": str(document.run_id or ""),
        "document_id": document.id,
        "application_id": document.application_id,
        "document_type": document.document_type,
        "generated_at": document.created_at.isoformat() if hasattr(document.created_at, "isoformat") else str(document.created_at),
        "manifest": _json_value(document.trace_json, {}),
        "generation_plan": _json_value(document.structured_content_json, {}),
        "reviewer": _json_value(document.reviewer_json, {}),
        "used_evidence_ids": _json_value(document.used_experiences_json, []),
        "final_output": document.content,
    }


def build_generation_trace(
    *,
    run_id: str,
    document_type: str,
    application_id: int,
    resume_id: int,
    provider: str,
    model: str,
    evidence_ids: list[str],
    reviewer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": GENERATION_TRACE_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "document_type": document_type,
        "input_refs": {"application_id": application_id, "resume_id": resume_id},
        "versions": {
            "prompt": DOCUMENT_PROMPT_VERSION,
            "ckb_schema": CKB_SCHEMA_VERSION,
            "job_model_schema": JOB_MODEL_SCHEMA_VERSION,
            "selection_plan_schema": SELECTION_PLAN_SCHEMA_VERSION,
            "review_schema": REVIEW_SCHEMA_VERSION,
            "government_writing_rules": GOVERNMENT_WRITING_RULES_VERSION,
            "applicant_profile_schema": APPLICANT_PROFILE_SCHEMA_VERSION,
        },
        "model": {"provider": provider, "name": model},
        "trace": {"evidence_ids": sorted(set(evidence_ids))},
        "review": {
            "status": str((reviewer or {}).get("status") or "not_run"),
            "finding_count": sum(len(item.get("issues") or []) for item in (reviewer or {}).get("results") or []),
        },
    }
