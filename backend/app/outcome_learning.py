import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4


OUTCOME_SCHEMA_VERSION = "1.0"
EVENT_TYPES = {"submitted", "interview", "progressed", "rejected", "offer", "withdrawn", "no_response", "unknown"}
POSITIVE_EVENTS = {"interview", "progressed", "offer"}
MARKETS = {
    "AU": "AU", "AUSTRALIA": "AU", "NZ": "NZ", "NEW ZEALAND": "NZ",
    "GB": "GB", "UK": "GB", "UNITED KINGDOM": "GB", "IE": "IE", "IRELAND": "IE",
    "US": "US", "USA": "US", "UNITED STATES": "US", "CA": "CA", "CANADA": "CA",
}


def empty_outcome() -> dict[str, Any]:
    return {"schema_version": OUTCOME_SCHEMA_VERSION, "current_outcome": "unknown", "excluded_from_learning": False, "events": [], "submission_snapshot": None, "submission_snapshot_status": "not_captured"}


def load_outcome(value: str | dict | None) -> dict[str, Any]:
    try:
        result = json.loads(value or "{}") if isinstance(value, str) else dict(value or {})
    except (json.JSONDecodeError, TypeError, ValueError):
        result = {}
    if result.get("schema_version") != OUTCOME_SCHEMA_VERSION:
        return empty_outcome()
    result.setdefault("events", [])
    result.setdefault("excluded_from_learning", False)
    result.setdefault("submission_snapshot", None)
    result.setdefault("submission_snapshot_status", "captured" if result["submission_snapshot"] else "not_captured")
    result["current_outcome"] = result["events"][-1]["event_type"] if result["events"] else "unknown"
    return result


def validate_outcome(outcome: dict[str, Any]) -> list[str]:
    errors = []
    if outcome.get("schema_version") != OUTCOME_SCHEMA_VERSION:
        errors.append("Unsupported outcome schema version.")
    if not isinstance(outcome.get("excluded_from_learning"), bool):
        errors.append("excluded_from_learning must be boolean.")
    events = outcome.get("events")
    if not isinstance(events, list):
        return errors + ["Outcome events must be a list."]
    ids = set()
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") not in EVENT_TYPES:
            errors.append("Invalid outcome event type.")
            continue
        if not str(event.get("event_id") or "") or event["event_id"] in ids:
            errors.append("Outcome event IDs must be present and unique.")
        ids.add(event.get("event_id"))
        try:
            date.fromisoformat(str(event.get("effective_date") or ""))
            datetime.fromisoformat(str(event.get("recorded_at") or "").replace("Z", "+00:00"))
        except ValueError:
            errors.append("Outcome event dates are invalid.")
    expected = events[-1]["event_type"] if events else "unknown"
    if outcome.get("current_outcome") != expected:
        errors.append("current_outcome must be derived from the final event.")
    return errors


def outcome_event(event_type: str, effective_date: date, stage_label: str = "", reason: str = "", note: str = "", event_id: str | None = None) -> dict[str, str]:
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported outcome event type.")
    return {
        "event_id": event_id or str(uuid4()), "event_type": event_type,
        "effective_date": effective_date.isoformat(), "recorded_at": datetime.now(timezone.utc).isoformat(),
        "stage_label": stage_label.strip(), "reason": reason.strip(), "note": note.strip(),
    }


def set_events(outcome: dict, events: list[dict]) -> dict:
    result = {**outcome, "schema_version": OUTCOME_SCHEMA_VERSION, "events": events}
    result["current_outcome"] = events[-1]["event_type"] if events else "unknown"
    errors = validate_outcome(result)
    if errors:
        raise ValueError(errors[0])
    return result


def source_hash(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def normalized_title(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def normalized_market(value: str | None) -> str | None:
    return MARKETS.get(str(value or "").strip().upper())


def build_submission_snapshot(application: Any, documents: list[Any], ckb: list[dict], market: str | None, sources: list[Any], submitted_at: datetime, required_document_types: tuple[str, ...] = ("tailored_resume", "cover_letter", "selection_criteria")) -> dict | None:
    latest = {}
    for document in sorted(documents, key=lambda item: (item.created_at, item.id or 0), reverse=True):
        latest.setdefault(document.document_type, document)
    if "tailored_resume" not in latest:
        return None
    ckb_by_id = {str(item.get("evidence_id")): item for item in ckb if isinstance(item, dict)}
    document_snapshots = {}
    used_ids = set()
    for document_type in required_document_types:
        document = latest.get(document_type)
        if not document:
            continue
        try:
            plan = json.loads(document.structured_content_json or "{}")
            used = [str(value) for value in json.loads(document.used_experiences_json or "[]")]
        except (json.JSONDecodeError, TypeError):
            plan, used = {}, []
        used_ids.update(used)
        document_snapshots[document_type] = {
            "document_id": document.id, "run_id": str(document.run_id or ""), "content": document.content,
            "plan": plan, "used_evidence_ids": used,
        }
    identities = [{
        "evidence_id": evidence_id,
        "evidence_type": str(ckb_by_id[evidence_id].get("evidence_type") or ""),
        "source_text_hash": source_hash(str(ckb_by_id[evidence_id].get("source_text") or "")),
    } for evidence_id in sorted(used_ids & set(ckb_by_id))]

    def parsed(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value or "")
        except (json.JSONDecodeError, TypeError):
            return fallback

    return {
        "schema_version": "1.0", "submitted_at": submitted_at.isoformat(),
        "documents": document_snapshots, "evidence_identities": identities,
        "job_model": parsed(application.job_model_json, {}),
        "application_requirements": parsed(application.application_requirements_json, {}),
        "application_decision": parsed(application.application_decision_json, {}),
        "market": normalized_market(market), "normalized_position_title": normalized_title(application.position_title),
        "source": {"job_url": application.job_url, "items": [{
            "source_id": item.source_id, "source_type": item.source_type, "source_url": item.source_url,
            "content_sha256": item.content_sha256,
        } for item in sources]},
        "export": {"format": None, "template": None},
    }


def build_outcome_signals(applications: list[Any], current_application_id: int | None, market: str | None, position_title: str, ckb: list[dict]) -> dict[str, Any]:
    target_market, target_title = normalized_market(market), normalized_title(position_title)
    current_identity = {str(item.get("evidence_id")): source_hash(str(item.get("source_text") or "")) for item in ckb if item.get("evidence_id")}
    comparable = []
    for application in applications:
        if application.id == current_application_id:
            continue
        outcome = load_outcome(application.outcome_json)
        snapshot = outcome.get("submission_snapshot")
        if outcome.get("excluded_from_learning") or not snapshot or not outcome.get("events"):
            continue
        if not target_market or snapshot.get("market") != target_market or snapshot.get("normalized_position_title") != target_title:
            continue
        comparable.append((outcome, snapshot))
    if len(comparable) < 3:
        return {"schema_version": "1.0", "status": "insufficient_history", "comparable_applications": len(comparable), "evidence_signals": []}
    stats: dict[str, dict[str, int]] = {}
    for outcome, snapshot in comparable:
        event_types = {item.get("event_type") for item in outcome["events"]}
        progressed = bool(event_types & {"interview", "progressed"})
        positive = bool(event_types & POSITIVE_EVENTS)
        offered = "offer" in event_types
        for identity in snapshot.get("evidence_identities") or []:
            evidence_id = str(identity.get("evidence_id"))
            if current_identity.get(evidence_id) != identity.get("source_text_hash"):
                continue
            item = stats.setdefault(evidence_id, {"applications_used": 0, "positive": 0, "progressed": 0, "offers": 0})
            item["applications_used"] += 1
            item["positive"] += int(positive)
            item["progressed"] += int(progressed)
            item["offers"] += int(offered)
    signals = [{
        "evidence_id": evidence_id, "identity_match": True,
        "applications_used": values["applications_used"],
        "interviews_or_progressions": values["progressed"], "offers": values["offers"],
        "positive_applications": values["positive"],
        "signal": "positive", "confidence": "low", "effect": "tie_break_only",
        "explanation": f"Used in {values['applications_used']} comparable submitted applications; {values['positive']} reached interview, progression or offer.",
    } for evidence_id, values in sorted(stats.items()) if values["positive"] >= 2]
    return {"schema_version": "1.0", "status": "available" if signals else "insufficient_history", "comparable_applications": len(comparable), "evidence_signals": signals}
