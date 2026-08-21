import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from .application_requirements import material_requirements_unknown


APPLICATION_DECISION_SCHEMA_VERSION = "1.0"
IMPORTANCE = {"essential", "desirable", "unknown"}
EVIDENCE_CLASSIFICATIONS = {"verified_match", "adjacent_match", "unverified_possible", "confirmed_gap"}
HARD_GATE_STATUSES = {"not_applicable", "pass", "fail", "unverified"}
ACTIONS = {"use", "reframe", "ask_user", "disclose", "omit"}
DISCLOSURES = {"none", "bridge", "explicit_gap"}
RECOMMENDATIONS = {"apply", "apply_with_caveats", "reconsider", "do_not_apply"}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def decision_inputs(job_model: dict, requirements: dict, ckb: list, profile: Any | None) -> dict[str, str]:
    profile_facts = {
        "work_rights": getattr(profile, "work_rights", None),
        "availability_notice": getattr(profile, "availability_notice", None),
    }
    normalised_requirements = deepcopy(requirements)
    for document in (normalised_requirements.get("documents") or {}).values():
        document.setdefault("basis", "unknown")
    return {
        "job_model": fingerprint(job_model),
        "application_requirements": fingerprint(normalised_requirements),
        "ckb": fingerprint(ckb),
        "profile": fingerprint(profile_facts),
    }


def decision_is_current(decision: dict, inputs: dict[str, str]) -> bool:
    return decision.get("schema_version") == APPLICATION_DECISION_SCHEMA_VERSION and decision.get("inputs") == inputs


def _importance(criterion: dict) -> str:
    return {"essential": "essential", "desirable": "desirable"}.get(str(criterion.get("criteria_type")), "unknown")


def _is_hard_gate(criterion: dict) -> bool:
    if _importance(criterion) != "essential":
        return False
    text = str(criterion.get("criteria_text") or "")
    return bool(re.search(
        r"(?i)\b(?:licen[cs]e|registration|citizenship|resident|work rights?|clearance|police check|"
        r"working with children|degree|certificate|qualification)\b", text
    ))


def _is_material_recoverable(criterion: dict) -> bool:
    return _importance(criterion) == "essential" and bool(re.search(
        r"(?i)\b(?:experience|track record|previously|qualification|degree|certificate|licen[cs]e|registration|"
        r"citizenship|resident|work rights?|clearance|police check|working with children)\b",
        str(criterion.get("criteria_text") or ""),
    ))


def _question_id(criteria_id: str) -> str:
    return "Q" + hashlib.sha1(criteria_id.encode()).hexdigest()[:10].upper()


def build_application_decision(
    job_model: dict,
    application_requirements: dict,
    matches: dict,
    ckb: list,
    profile: Any | None = None,
    previous: dict | None = None,
) -> dict[str, Any]:
    match_by_id = {str(item.get("criteria_id")): item for item in matches.get("matches") or []}
    previous_answers = {
        str(item.get("question_id")): item
        for item in (previous or {}).get("questions") or []
        if item.get("answer") is not None
    }
    requirements = []
    questions = []
    blocking_issues = []
    materially_incomplete = application_requirements.get("completeness") == "incomplete" or material_requirements_unknown(application_requirements)
    if materially_incomplete:
        blocking_issues.append({
            "criteria_id": "",
            "code": "employer_requirements_incomplete",
            "message": "Employer requirements are incomplete. Acquire or upload the referenced JDF or application instructions, then confirm the complete requirements before generating documents.",
        })

    for criterion in job_model.get("criteria") or []:
        criteria_id = str(criterion.get("criteria_id"))
        match = match_by_id.get(criteria_id, {})
        match_type = str(match.get("match_type") or "insufficient")
        coverage = str(match.get("coverage") or "weak")
        importance = _importance(criterion)
        gate = _is_hard_gate(criterion)
        material_recoverable = _is_material_recoverable(criterion)
        question_id = _question_id(criteria_id)
        answer_record = previous_answers.get(question_id)
        answer = answer_record.get("answer") if answer_record else None
        matched_evidence = [str(value) for value in match.get("matched_evidence") or []]

        if matched_evidence and match_type == "direct":
            classification, gate_status = "verified_match", "pass" if gate else "not_applicable"
            risk, action, disclosure = "low", "use", "none"
        elif matched_evidence:
            classification, gate_status = "adjacent_match", "unverified" if gate else "not_applicable"
            risk, action, disclosure = ("high", "ask_user", "none") if gate else ("medium", "reframe", "bridge")
        elif material_recoverable and answer is None:
            classification, gate_status = "unverified_possible", "unverified" if gate else "not_applicable"
            risk, action, disclosure = ("high" if gate else "medium"), "ask_user", "none"
        elif material_recoverable and answer is False:
            classification, gate_status = "confirmed_gap", "fail" if gate else "not_applicable"
            risk, action, disclosure = "high", "omit", "none"
        elif material_recoverable and answer is True:
            # Confirmation can satisfy an eligibility decision, but is not generator evidence.
            classification, gate_status = "unverified_possible", "pass" if gate else "not_applicable"
            risk, action, disclosure = "medium", "omit", "none"
        else:
            # No candidate-fact conclusion is made from an absent CKB match.
            classification, gate_status = None, "not_applicable"
            risk = "low" if importance in {"desirable", "unknown"} else "medium"
            action, disclosure = "omit", "none"

        if gate and answer is False:
            classification, gate_status, risk, action, disclosure = "confirmed_gap", "fail", "high", "omit", "none"
        elif gate and answer is True:
            gate_status = "pass"

        if material_recoverable and (not matched_evidence or gate_status == "unverified"):
            prior = previous_answers.get(question_id, {})
            questions.append({
                "question_id": question_id,
                "criteria_id": criteria_id,
                "prompt": f"Can you confirm that you meet this requirement: {criterion.get('criteria_text', '')}",
                "material": True,
                "answer": answer,
                "provenance": prior.get("provenance"),
                "answered_at": prior.get("answered_at"),
            })
        if gate_status == "fail":
            blocking_issues.append({"criteria_id": criteria_id, "code": "hard_gate_failed", "message": str(criterion.get("criteria_text") or "Eligibility requirement not met.")})

        requirements.append({
            "criteria_id": criteria_id,
            "requirement_text": str(criterion.get("criteria_text") or ""),
            "importance": importance,
            "hard_gate_status": gate_status,
            "evidence_classification": classification,
            "matched_evidence": matched_evidence,
            "risk": risk,
            "recommended_action": action,
            "disclosure_strategy": disclosure,
        })

    unresolved_material = any(item["material"] and item.get("answer") is None for item in questions)
    if any(item["code"] == "hard_gate_failed" for item in blocking_issues):
        status, recommendation = "blocked", "do_not_apply"
    elif materially_incomplete:
        status, recommendation = "needs_confirmation", "reconsider"
    elif unresolved_material:
        status, recommendation = "needs_confirmation", "reconsider"
    elif any(item["importance"] == "essential" and item["evidence_classification"] == "confirmed_gap" for item in requirements):
        status, recommendation = "ready", "reconsider"
    elif any(item["risk"] in {"medium", "high"} for item in requirements):
        status, recommendation = "ready", "apply_with_caveats"
    else:
        status, recommendation = "ready", "apply"
    return {
        "schema_version": APPLICATION_DECISION_SCHEMA_VERSION,
        "status": status,
        "inputs": decision_inputs(job_model, application_requirements, ckb, profile),
        "requirements": requirements,
        "blocking_issues": blocking_issues,
        "questions": questions,
        "application_recommendation": recommendation,
    }


def validate_application_decision(decision: dict) -> list[str]:
    errors = []
    if decision.get("schema_version") != APPLICATION_DECISION_SCHEMA_VERSION:
        errors.append("Unsupported Application Decision schema version.")
    if decision.get("status") not in {"needs_confirmation", "ready", "blocked"}:
        errors.append("Invalid Application Decision status.")
    if decision.get("application_recommendation") not in RECOMMENDATIONS:
        errors.append("Invalid application recommendation.")
    for item in decision.get("requirements") or []:
        if item.get("importance") not in IMPORTANCE:
            errors.append("Invalid requirement importance.")
        if item.get("evidence_classification") is not None and item.get("evidence_classification") not in EVIDENCE_CLASSIFICATIONS:
            errors.append("Invalid evidence classification.")
        if item.get("hard_gate_status") not in HARD_GATE_STATUSES:
            errors.append("Invalid hard-gate status.")
        if item.get("recommended_action") not in ACTIONS or item.get("disclosure_strategy") not in DISCLOSURES:
            errors.append("Invalid decision action or disclosure strategy.")
    return errors
