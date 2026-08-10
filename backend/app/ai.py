from datetime import date
import json
import re

from openai import OpenAI, OpenAIError
from .config import settings
from .evidence_matcher import matched_evidence_pack, normalise_match_result, validate_match_result
from .government_writing_rules import government_writing_rules
from .selection_logic import hard_validate_response
from .reviewer import normalise_review_result, validate_review_result
from .reviewer_core import normalise_document_review
from .ai_runtime import ai_call_scope, record_ai_call, start_ai_call
from .resume_plan import resume_evidence_pack


EVIDENCE_STOP_WORDS = {
    "about", "after", "also", "and", "are", "for", "from", "have", "into", "job",
    "role", "that", "the", "their", "this", "with", "will", "you", "your",
}


def _evidence_tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+#-]{2,}", text)
        if token.lower() not in EVIDENCE_STOP_WORDS
    }


def build_evidence_pack(
    master_resume: str,
    user_experiences_json: str,
    job_description: str,
    selection_criteria: str | None = None,
    max_items: int = 8,
) -> list[dict[str, str]]:
    """Build a small, traceable set of resume evidence for document generation."""
    evidence: list[dict[str, str]] = []
    try:
        experiences = json.loads(user_experiences_json or "[]")
    except json.JSONDecodeError:
        experiences = []
    if isinstance(experiences, list):
        for index, item in enumerate(experiences, start=1):
            if not isinstance(item, dict):
                continue
            source_text = str(item.get("source_text") or "").strip() or ". ".join(
                str(item.get(field, "")).strip() for field in (
                    "role_title", "organization", "responsibility", "context", "result", "detail"
                ) if str(item.get(field, "")).strip()
            )
            if source_text:
                evidence.append({
                    "evidence_id": str(item.get("evidence_id") or item.get("id") or f"EXP{index:03d}"),
                    "evidence_type": str(item.get("evidence_type") or "experience"),
                    "source_section": str(item.get("source_section") or "Master Resume"),
                    "source_text": source_text,
                })
    if not evidence:
        resume_lines = [line.strip(" \t•-–—") for line in master_resume.splitlines() if line.strip()]
        for index in range(0, len(resume_lines), 2):
            source_text = " ".join(resume_lines[index:index + 2]).strip()
            if len(source_text) >= 30:
                evidence.append({"evidence_id": f"RES{index // 2 + 1:03d}", "source_text": source_text})
    query_tokens = _evidence_tokens(f"{job_description}\n{selection_criteria or ''}")
    ranked = sorted(
        evidence,
        key=lambda item: (len(_evidence_tokens(item["source_text"]) & query_tokens), len(item["source_text"])),
        reverse=True,
    )
    return ranked[:max_items]


def selection_input_mode(selection_criteria: str | None) -> str:
    value = (selection_criteria or "").strip()
    if not value:
        return "not provided"
    meaningful_lines = [line for line in value.splitlines() if line.strip()]
    if len(value) <= 220 and len(meaningful_lines) <= 3:
        return "brief user guidance"
    return "full selection criteria"

def target_english_variant() -> str:
    return settings.target_english_variant.strip() or "Australian English"


def safety_instruction() -> str:
    variant = target_english_variant()
    return f"""You are a careful Australian job-application writer.

{government_writing_rules(variant)}

Only use facts found in the supplied Master Resume, CKB and Applicant Profile. A Job Description is an employer requirement, never applicant evidence. Never upgrade assisted to prepared or delivered, supported to managed or led, liaised to took responsibility, or participation to ownership. Do not add performance, quality or result claims such as accurate, timely, successful, adaptable or kept deliverables on track unless the evidence says so explicitly. Never compare the value, scale, complexity or significance of two projects unless both sources provide explicit, verifiable facts supporting that comparison. When a named system is absent from the evidence, do not claim proficiency, comfort, fast learning or quick adaptation; if useful, state the gap plainly and refer only to genuinely analogous tools or processes. Avoid subjective suitability claims such as 'proven capability', 'strong record', 'I am confident', 'I am excited', 'I am well placed', 'I am comfortable learning', 'I can adapt quickly', 'I am writing to express my interest', 'proven track record', 'dynamic professional', 'passionate about', or 'leverage my skills'. Treat 'Do not state' and 'Not provided' Applicant Profile values as explicit prohibitions: omit them rather than filling gaps. Never invent work rights, residency, visa status, notice period, availability, salary, motivation, values alignment or career goals. A neutral application statement is allowed, but personal motivation is allowed only when the Applicant Profile explicitly supplies it. Never calculate or invent a calendar start date from a notice period; use the exact confirmed availability wording supplied in the Applicant Profile. Never use American spelling when the configured English variant uses a different standard spelling."""


class AIServiceError(Exception):
    """A safe, user-facing failure when the AI provider cannot generate a draft."""


def _json_object(value: str) -> dict:
    cleaned = value.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("The AI matcher did not return a JSON object.")
    return parsed


def _openai_draft(prompt: str) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    client = OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.ai_request_timeout_seconds,
        max_retries=0,
    )
    call = start_ai_call()
    try:
        response = client.responses.create(model=settings.openai_model, input=prompt)
    except Exception:
        record_ai_call(
            call, provider="openai", model=settings.openai_model,
            input_tokens=0, output_tokens=0, estimated_cost=0.0, status="failed",
        )
        raise
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    estimated_cost = (
        input_tokens * settings.openai_input_cost_per_million
        + output_tokens * settings.openai_output_cost_per_million
    ) / 1_000_000
    record_ai_call(
        call, provider="openai", model=settings.openai_model,
        input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=estimated_cost,
    )
    return response.output_text


def _deepseek_draft(prompt: str) -> str:
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured.")
    client = OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.ai_request_timeout_seconds,
        max_retries=0,
    )
    call = start_ai_call()
    try:
        response = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": safety_instruction()},
                {"role": "user", "content": prompt},
            ],
            stream=False,
            max_tokens=2000,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception:
        record_ai_call(
            call, provider="deepseek", model=settings.deepseek_model,
            input_tokens=0, output_tokens=0, estimated_cost=0.0, status="failed",
        )
        raise
    content = response.choices[0].message.content
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    estimated_cost = (
        input_tokens * settings.deepseek_input_cost_per_million
        + output_tokens * settings.deepseek_output_cost_per_million
    ) / 1_000_000
    record_ai_call(
        call, provider="deepseek", model=settings.deepseek_model,
        input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=estimated_cost,
    )
    if not content:
        raise AIServiceError("DeepSeek returned an empty draft. Please try again.")
    return content


def match_evidence_batch(ckb_json: str, job_model_json: str) -> dict:
    try:
        ckb = json.loads(ckb_json or "[]")
        job_model = json.loads(job_model_json or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("CKB or Job Model JSON is invalid.") from error
    if not isinstance(ckb, list) or not isinstance(job_model, dict):
        raise ValueError("CKB or Job Model has the wrong structure.")
    if not job_model.get("criteria"):
        return {"schema_version": "1.0", "matches": [], "unused_evidence": [str(item.get("evidence_id")) for item in ckb if item.get("evidence_id")]}
    prompt = f"""You are a Selection Criteria evidence-matching assistant. Match all criteria in one batch so evidence choices are consistent across the application.

Rules:
- Return at most three evidence IDs per criterion, ranked by relevance.
- Use match_type direct only for a clear evidence-to-requirement match.
- Use inferred for genuinely adjacent or transferable evidence.
- Use insufficient when no supportable evidence exists.
- coverage must be strong, partial, or weak.
- Relevance and factual strength outrank diversity. Prefer recency only between similarly strong evidence.
- Do not invent evidence IDs. Do not downgrade a materially stronger match to create variety.
- Return every criterion exactly once and list evidence not used anywhere in unused_evidence.

SHARED JOB MODEL:
{json.dumps(job_model, ensure_ascii=False)}

CAREER KNOWLEDGE BASE:
{json.dumps(ckb, ensure_ascii=False)}

Return JSON only in this shape:
{{"matches":[{{"criteria_id":"...","matched_evidence":["EV..."],"match_type":"direct|inferred|insufficient","coverage":"strong|partial|weak","reasoning":"one sentence"}}],"unused_evidence":["EV..."]}}"""
    provider = settings.ai_provider.strip().lower()
    try:
        if provider == "deepseek":
            with ai_call_scope("matching"):
                raw = _deepseek_draft(prompt)
        elif provider == "openai":
            try:
                with ai_call_scope("matching"):
                    raw = _openai_draft(prompt)
            except OpenAIError:
                if not settings.ai_fallback_to_deepseek:
                    raise
                with ai_call_scope("matching", "provider_fallback"):
                    raw = _deepseek_draft(prompt)
        else:
            raise ValueError("AI_PROVIDER must be either 'openai' or 'deepseek'.")
        normalised = normalise_match_result(_json_object(raw), job_model, ckb)
    except (OpenAIError, json.JSONDecodeError) as error:
        raise AIServiceError("Evidence matching failed. Please try again.") from error
    errors = validate_match_result(normalised, job_model, ckb)
    if errors:
        raise AIServiceError(errors[0])
    return normalised


def _selection_provider_response(prompt: str) -> str:
    provider = settings.ai_provider.strip().lower()
    if provider == "deepseek":
        return _deepseek_draft(prompt)
    if provider != "openai":
        raise ValueError("AI_PROVIDER must be either 'openai' or 'deepseek'.")
    try:
        return _openai_draft(f"{safety_instruction()}\n\n{prompt}")
    except OpenAIError:
        if not settings.ai_fallback_to_deepseek:
            raise
        return _deepseek_draft(prompt)


def _extractive_criterion_response(plan_item: dict, matched: list[dict]) -> dict:
    evidence_ids: list[str] = []
    statements: list[str] = []
    for evidence in matched:
        evidence_id = str(evidence.get("evidence_id") or "")
        source = str(evidence.get("source_text") or "").strip(" \t-鈥?")
        section = str(evidence.get("source_section") or "the supplied resume").strip()
        if evidence_id:
            evidence_ids.append(evidence_id)
        if source:
            statements.append(f"Under {section}, the supplied resume states: {source}")
    final_response = " ".join(statements)
    if final_response:
        final_response += " No experience beyond these supplied records is claimed."
    else:
        final_response = (
            "The supplied resume does not contain evidence for this criterion. "
            "No additional experience is claimed."
        )
    return {
        "criteria_id": str(plan_item.get("criteria_id") or ""),
        "evidence_used": evidence_ids,
        "star": {
            "situation": "Source-preserving fallback",
            "task": "Address the criterion without inference",
            "action": "Present only matched resume evidence",
            "result": "No unsupported result claimed",
        },
        "final_response": final_response,
        "word_count": len(re.findall(r"\b[\w'-]+\b", final_response, flags=re.UNICODE)),
        "validation": {"valid": True, "issues": [], "fallback": "extractive"},
    }


def generate_selection_criteria_bundle(
    ckb_json: str,
    selection_plan_json: str,
    existing_bundle: dict | None = None,
    review_feedback: dict[str, list[dict]] | None = None,
) -> dict:
    try:
        ckb = json.loads(ckb_json or "[]")
        plan = json.loads(selection_plan_json or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("CKB or Selection Plan JSON is invalid.") from error
    if not isinstance(ckb, list) or not isinstance(plan, dict) or not plan.get("items"):
        raise ValueError("No Selection Criteria plan is available for generation.")
    evidence_by_id = {str(item.get("evidence_id")): item for item in ckb if isinstance(item, dict)}
    responses: list[dict] = []
    used_ids: list[str] = []
    generator_retries = int((existing_bundle or {}).get("telemetry", {}).get("generator_retries", 0))
    corrected_criteria = 0
    existing_responses = {
        str(item.get("criteria_id")): item
        for item in (existing_bundle or {}).get("responses") or []
        if isinstance(item, dict)
    }
    for plan_item in plan["items"]:
        criteria_id = str(plan_item.get("criteria_id") or "")
        if review_feedback is not None and criteria_id not in review_feedback and criteria_id in existing_responses:
            response = existing_responses[criteria_id]
            responses.append(response)
            for evidence_id in response.get("evidence_used") or []:
                if evidence_id not in used_ids:
                    used_ids.append(evidence_id)
            continue
        matched = [evidence_by_id[value] for value in plan_item.get("matched_evidence") or [] if value in evidence_by_id]
        correction_context = ""
        if review_feedback is not None:
            corrected_criteria += 1
            correction_context = (
                "\n\nCORRECTION REQUIRED. Rewrite the complete response, preserving supported facts only.\n"
                f"PREVIOUS RESPONSE:\n{json.dumps(existing_responses.get(criteria_id, {}), ensure_ascii=False)}\n"
                f"REVIEWER FINDINGS TO CORRECT:\n{json.dumps(review_feedback.get(criteria_id, []), ensure_ascii=False)}"
            )
        base_prompt = f"""You are writing one Selection Criterion response for an Australian government application.

{government_writing_rules(target_english_variant())}

If evidence is transferable or weak, frame it conservatively. If evidence is insufficient, do not fabricate a story.
Do not claim that education developed skills unless the evidence explicitly says so. Do not add self-assessments such as
strong, organised, proactive, fast-paced, complex, effective, conscientious, methodical, careful, able to deliver,
able to build relationships, or accustomed to checking work. Do not describe how experience developed or strengthened
an ability. Do not infer hidden steps such as checking figures, resolving discrepancies, invoice processing or budget
monitoring. Coordination is not management. If a requested capability is absent, state the exact supported transferable
work and the exact gap briefly, without inventing willingness, future training, interview availability or personal qualities.
Attribute every duty to the specific role whose evidence contains it. Never combine different duties from several roles
into a claim that all roles, or any one role, covered the combined scope. State a qualification by its exact title only;
do not infer its subjects, learning outcomes, relevance, equivalence or acceptance under the employer's criterion.

CRITERION PLAN:
{json.dumps(plan_item, ensure_ascii=False)}

MATCHED CKB EVIDENCE (the only factual source):
{json.dumps(matched, ensure_ascii=False)}

Write about {plan_item.get('allocated_word_limit')} words. Return JSON only:
{{"criteria_id":"{plan_item.get('criteria_id')}","evidence_used":["EV..."],"star":{{"situation":"...","task":"...","action":"...","result":"..."}},"final_response":"natural paragraph text","word_count":0}}

Every evidence_used ID must appear in CRITERION PLAN matched_evidence. Include only IDs materially used in final_response. The STAR fields are audit fields; do not print S/T/A/R labels inside final_response.{correction_context}"""
        validation = None
        response: dict = {}
        for attempt in range(2):
            retry_note = ""
            if validation and validation["issues"]:
                retry_note = "\n\nYour previous output failed deterministic validation. Correct only these issues:\n" + json.dumps(validation["issues"], ensure_ascii=False)
            try:
                with ai_call_scope("generation", "deterministic_validation_failed" if retry_note else ""):
                    response = _json_object(_selection_provider_response(base_prompt + retry_note))
            except (OpenAIError, ValueError) as error:
                if attempt == 1:
                    validation = {"valid": False, "issues": [{
                        "code": "invalid_json",
                        "message": "The model did not return valid JSON after one retry.",
                    }]}
                    break
                validation = {"issues": [{"code": "invalid_json", "message": "Return one valid JSON object only."}]}
                continue
            if str(response.get("criteria_id") or "") != str(plan_item.get("criteria_id")):
                validation = {"issues": [{"code": "criteria_mismatch", "message": "Return the exact criteria_id from the plan."}]}
            else:
                validation = hard_validate_response(response, plan_item)
            if validation.get("valid"):
                break
        if not validation or not validation.get("valid"):
            response = _extractive_criterion_response(plan_item, matched)
            corrected_criteria += 1
        else:
            response["word_count"] = validation["actual_word_count"]
            response["validation"] = validation
        generator_retries += attempt
        responses.append(response)
        for evidence_id in response.get("evidence_used") or []:
            if evidence_id not in used_ids:
                used_ids.append(evidence_id)
    content = "\n\n".join(
        f"## {item['plan']['criteria_text']}\n\n{item['response']['final_response'].strip()}"
        for item in ({"plan": plan_item, "response": response} for plan_item, response in zip(plan["items"], responses))
    )
    return {
        "content": content,
        "responses": responses,
        "used_experiences": used_ids,
        "actual_total_word_count": sum(item["word_count"] for item in responses),
        "telemetry": {
            "generator_retries": generator_retries,
            "criterion_count": len(responses),
            "corrected_criteria": corrected_criteria,
        },
    }


def build_extractive_selection_fallback(
    ckb_json: str,
    selection_plan_json: str,
    existing_bundle: dict,
    failed_criteria_ids: set[str],
) -> dict:
    """Replace only persistently failing criteria with source-preserving prose."""
    ckb = json.loads(ckb_json or "[]")
    plan = json.loads(selection_plan_json or "{}")
    evidence_by_id = {
        str(item.get("evidence_id")): item for item in ckb if isinstance(item, dict)
    }
    existing_by_id = {
        str(item.get("criteria_id")): item
        for item in existing_bundle.get("responses") or []
        if isinstance(item, dict)
    }
    responses: list[dict] = []
    used_ids: list[str] = []
    fallback_count = 0
    for plan_item in plan.get("items") or []:
        criteria_id = str(plan_item.get("criteria_id") or "")
        if criteria_id not in failed_criteria_ids and criteria_id in existing_by_id:
            response = existing_by_id[criteria_id]
        else:
            fallback_count += 1
            matched = [
                evidence_by_id[str(value)] for value in plan_item.get("matched_evidence") or []
                if str(value) in evidence_by_id
            ]
            response = _extractive_criterion_response(plan_item, matched)
        responses.append(response)
        for evidence_id in response.get("evidence_used") or []:
            if evidence_id not in used_ids:
                used_ids.append(evidence_id)
    content = "\n\n".join(
        f"## {plan_item['criteria_text']}\n\n{response['final_response'].strip()}"
        for plan_item, response in zip(plan.get("items") or [], responses)
    )
    telemetry = dict(existing_bundle.get("telemetry") or {})
    telemetry["extractive_fallback_criteria"] = fallback_count
    return {
        "content": content,
        "responses": responses,
        "used_experiences": used_ids,
        "actual_total_word_count": sum(int(item.get("word_count") or 0) for item in responses),
        "telemetry": telemetry,
    }


def review_selection_criteria_batch(ckb_json: str, selection_plan_json: str, bundle: dict, master_resume: str = "") -> dict:
    try:
        ckb = json.loads(ckb_json or "[]")
        plan = json.loads(selection_plan_json or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("CKB or Selection Plan JSON is invalid.") from error
    criteria_ids = [str(item.get("criteria_id")) for item in plan.get("items") or []]
    if not criteria_ids or len(bundle.get("responses") or []) != len(criteria_ids):
        raise ValueError("The Reviewer package is incomplete.")
    package = [
        {"criterion": plan_item, "response": response}
        for plan_item, response in zip(plan["items"], bundle["responses"])
    ]
    prompt = f"""You are the factual quality Reviewer for a batch of Australian government Selection Criteria responses. Do not rewrite, improve or repair any response. Only verify and flag issues.

{government_writing_rules(target_english_variant())}

For every criterion, check only these issue types:
- unsupported_claim
- unsupported_inference
- fabricated_figure
- evidence_mismatch
- internal_inconsistency
- jd_wording_repeated
- ai_tone
- declared_evidence_unused
- unmatched_evidence_used

Do not evaluate exact word counts, JSON structure, evidence reuse percentages or employer share; deterministic application logic already checks them. A stylistic preference alone must not fail a response. When a response explicitly attributes wording to the supplied resume and preserves its source_text, accept that wording as evidence rather than treating the quotation itself as an inference.

BATCH PACKAGE:
{json.dumps(package, ensure_ascii=False)}

FULL CAREER KNOWLEDGE BASE WITH SOURCE TEXT:
{json.dumps(ckb, ensure_ascii=False)}

ORIGINAL MASTER RESUME (also authoritative for exact roles, employers, dates, education and qualifications):
{master_resume or 'Not provided'}

Return JSON only:
{{"results":[{{"criteria_id":"...","status":"pass|fail","issues":[{{"type":"unsupported_claim","description":"...","evidence":"source detail","location":"response phrase","recommended_action":"specific guidance"}}],"recommendation":"optional guidance"}}]}}

Return every criteria_id exactly once. Use pass with an empty issues array when no material issue exists."""
    last_error = ""
    for attempt in range(2):
        try:
            with ai_call_scope("review", "invalid_reviewer_result" if last_error else ""):
                raw = _json_object(_selection_provider_response(prompt + (f"\n\nPrevious validation error: {last_error}" if last_error else "")))
            result = normalise_review_result(raw, criteria_ids)
            errors = validate_review_result(result, criteria_ids)
            if not errors:
                result["telemetry"] = {"reviewer_retries": attempt}
                return result
            last_error = errors[0]
        except (OpenAIError, ValueError) as error:
            last_error = str(error)
    raise AIServiceError(f"Batch Reviewer failed validation: {last_error or 'unknown error'}")


def review_cover_letter(
    ckb_json: str,
    job_model_json: str,
    cover_letter_plan_json: str,
    applicant_profile: str | None,
    content: str,
    master_resume: str = "",
) -> dict:
    try:
        ckb = json.loads(ckb_json or "[]")
        job_model = json.loads(job_model_json or "{}")
        plan = json.loads(cover_letter_plan_json or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("Cover Letter Reviewer inputs are invalid.") from error
    if not content.strip() or not plan.get("priorities"):
        raise ValueError("The Cover Letter Reviewer package is incomplete.")
    prompt = f"""You are the factual and requirement-coverage Reviewer for an Australian government Cover Letter. Do not rewrite, improve or repair the letter. Only verify and flag material issues.

{government_writing_rules(target_english_variant())}

Check only these issue types:
- unsupported_claim
- unsupported_inference
- fabricated_figure
- evidence_mismatch
- internal_inconsistency
- contradiction
- unmatched_evidence_used
- unsupported_motivation
- requirement_omission
- jd_wording_repeated
- ai_tone
- declared_evidence_unused
- style_only

Applicant Profile motivation may support a motivational statement only when it is explicitly supplied and is not 'Not provided'. Target direction is not personal motivation. A neutral statement that the applicant is applying for the named role is allowed. Conventional neutral application language such as 'I would welcome the opportunity to contribute', 'I am happy to provide further information' and 'I look forward to hearing from you' is not personal motivation and must not be flagged. The CURRENT DATE supplied by the application is system context and must not be checked against resume evidence. Treat a broad statement about alignment or career-wide fit as style_only when it adds no duty, ability, result, motivation or personal value. Use unsupported_inference when wording adds an ability, responsibility, performance quality or result. Do not flag omission of a JD sub-task when the supplied evidence does not support it; the letter must never invent missing experience merely to cover every requirement. A cover letter may select the strongest evidence and is not required to restate every criterion. Never return an issue whose own description says the wording is supported, accurate, acceptable, neutral, not flagged or not material. A style_only preference must never cause failure by itself. Do not calculate exact word counts; application logic handles mechanical constraints.

SHARED JOB MODEL:
{json.dumps(job_model, ensure_ascii=False)}

COVER LETTER PLAN:
{json.dumps(plan, ensure_ascii=False)}

APPLICANT PROFILE DECLARATIONS:
{applicant_profile or 'Not provided'}

FULL CKB WITH SOURCE TEXT:
{json.dumps(ckb, ensure_ascii=False)}

ORIGINAL MASTER RESUME (also authoritative for exact roles, employers, dates, education and qualifications):
{master_resume or 'Not provided'}

FINAL COVER LETTER:
{content}

Return JSON only:
{{"status":"pass|fail","issues":[{{"type":"unsupported_claim","description":"...","evidence":"source detail","location":"letter phrase","recommended_action":"specific guidance"}}],"recommendation":"optional guidance"}}

Use pass with an empty issues array when there is no material issue."""
    last_error = ""
    for attempt in range(2):
        try:
            with ai_call_scope("review", "invalid_reviewer_result" if last_error else ""):
                raw = _json_object(_selection_provider_response(prompt + (f"\n\nPrevious validation error: {last_error}" if last_error else "")))
            result = normalise_document_review(raw, "cover_letter")
            result["telemetry"] = {"reviewer_retries": attempt}
            return result
        except (OpenAIError, ValueError) as error:
            last_error = str(error)
    raise AIServiceError(f"Cover Letter Reviewer failed validation: {last_error or 'unknown error'}")


def review_tailored_resume(
    ckb_json: str,
    job_model_json: str,
    resume_plan_json: str,
    content: str,
    master_resume: str = "",
    applicant_profile: str = "",
) -> dict:
    try:
        ckb = json.loads(ckb_json or "[]")
        job_model = json.loads(job_model_json or "{}")
        plan = json.loads(resume_plan_json or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("Resume Reviewer inputs are invalid.") from error
    if not content.strip() or not plan.get("selected_evidence"):
        raise ValueError("The Resume Reviewer package is incomplete.")
    prompt = f"""You are the factual and relevance Reviewer for an ATS-friendly Australian government tailored CV. Do not rewrite, improve or repair the CV. Only verify and flag material issues.

{government_writing_rules(target_english_variant())}

Check only these issue types:
- unsupported_claim
- unsupported_inference
- fabricated_figure
- evidence_mismatch
- internal_inconsistency
- contradiction
- unmatched_evidence_used
- requirement_omission
- jd_wording_repeated
- ai_tone
- style_only

Check that roles, employers, dates, responsibilities, skills and outcomes remain traceable to CKB source_text. Check whether the curation reflects the Resume Plan and the strongest job-relevant evidence. Do not penalise factual compression or reordering. A style_only preference must never cause failure by itself. Do not calculate exact word counts or required headings; application logic already checks them.

SHARED JOB MODEL:
{json.dumps(job_model, ensure_ascii=False)}

RESUME CURATION PLAN:
{json.dumps(plan, ensure_ascii=False)}

FULL CKB WITH SOURCE TEXT:
{json.dumps(ckb, ensure_ascii=False)}

ORIGINAL MASTER RESUME (also authoritative for exact roles, employers, dates, education and qualifications):
{master_resume or 'Not provided'}

APPLICANT PROFILE DECLARATIONS (authoritative for confirmed personal details such as residency and notice period):
{applicant_profile or 'Not provided'}

FINAL TAILORED CV:
{content}

Return JSON only:
{{"status":"pass|fail","issues":[{{"type":"unsupported_claim","description":"...","evidence":"source detail","location":"CV phrase","recommended_action":"specific guidance"}}],"recommendation":"optional guidance"}}

Use pass with an empty issues array when there is no material issue."""
    last_error = ""
    for attempt in range(2):
        try:
            with ai_call_scope("review", "invalid_reviewer_result" if last_error else ""):
                raw = _json_object(_selection_provider_response(prompt + (f"\n\nPrevious validation error: {last_error}" if last_error else "")))
            result = normalise_document_review(raw, "tailored_resume")
            result["telemetry"] = {"reviewer_retries": attempt}
            return result
        except (OpenAIError, ValueError) as error:
            last_error = str(error)
    raise AIServiceError(f"Resume Reviewer failed validation: {last_error or 'unknown error'}")


def _finalise_date(content: str) -> str:
    today = date.today()
    written_date = f"{today.day} {today.strftime('%B %Y')}"
    return re.sub(r"\[(?:current\s+)?date\]", written_date, content, flags=re.IGNORECASE)


def generate_draft(
    master_resume: str,
    job_description: str,
    document_type: str,
    selection_criteria: str | None = None,
    applicant_profile: str | None = None,
    position_title: str | None = None,
    company: str | None = None,
    user_experiences_json: str = "[]",
    used_experiences: str = "[]",
    used_closing_styles: str = "[]",
    structured_job_model: str = "{}",
    evidence_matches_json: str = "{}",
    selection_plan_json: str = "{}",
    cover_letter_plan_json: str = "{}",
    resume_plan_json: str = "{}",
    correction_instructions: str = "",
) -> str:
    provider = settings.ai_provider.strip().lower()
    if provider not in {"openai", "deepseek"}:
        raise ValueError("AI_PROVIDER must be either 'openai' or 'deepseek'.")
    task = {
        "tailored_resume": "Curate a clean ATS-friendly CV according to the deterministic RESUME CURATION PLAN, targeting about 550-750 words and no more than two pages when exported. Use these exact Markdown section headings in this order: '## Professional Summary', '## Key Skills', and '## Work Experience'. Finish with '## References' followed by 'Available upon request'. Treat the selected CKB evidence as the only source for employment bullets. Preserve the source responsibility level exactly; concise action-led wording must not turn assistance into preparation, support into management, liaison into ownership, or participation into leadership. Use a supplied exact result or rough range when available; when result is blank or unavailable, omit the outcome instead of inventing a qualitative result. Use present tense for the current role and past tense for earlier roles. Keep original employers, titles and dates truthful. Do not use tables, columns, graphics, first-person pronouns, selection criteria, reviewer notes or match scores. After the submission-ready CV, add exactly one machine-readable line: <!-- GENERATION_META {\"used_experiences\":[\"evidence-id\"],\"closing_styles\":[]} -->. Every evidence ID must exist in RESUME CURATION PLAN selected_evidence.",
        "cover_letter": "Write a concise one-page cover letter of about 300-450 words. State the exact target position at least once. Never use 'RE:' or 'Subject:' as a heading; use a natural 'Application for [position]' heading if a heading is helpful. This letter must work as a standalone companion to the CV even when no selection criteria document exists. Select only one or two of the strongest items from MATCHED RESUME EVIDENCE and summarise them briefly instead of retelling the CV. Preserve each source's responsibility level exactly and do not add adaptability, delivery, quality, timeliness, ownership or results that the evidence does not state. Respond to the role requirements by connecting them to named, supplied evidence; do not copy distinctive JD phrases or treat JD duties as applicant experience. Never say an experience directly aligns with an employer need and never reproduce a list of employer activities; describe only the applicant evidence in original wording. Use a neutral application-intent sentence when motivation is not explicitly supplied. Explain personal motivation, work meaning, values alignment or career goals only when Applicant Profile Motivation explicitly provides that information. When evidence IDs were detailed in selection criteria, those items may receive at most one brief sentence total. If the JD mentions roster/shift work, medical checks, right to work, police clearance or a licence, include a confirmation only when the Applicant Profile explicitly supplies it; 'Do not state' and 'Not provided' mean omit it. Begin with the supplied date. Use 'Yours sincerely' only for a named addressee; use 'Yours faithfully' after a generic salutation. After the submission-ready letter, add exactly one machine-readable line: <!-- GENERATION_META {\"used_experiences\":[\"evidence-id\"],\"closing_styles\":[]} -->.",
        "selection_criteria": "Respond separately to every supplied criterion. Every response must follow a natural Situation–Task–Action–Result flow without printing S/T/A/R labels. Use only MATCHED RESUME EVIDENCE. Never treat the JD as proof of applicant experience. Use only results supplied by the user; never invent a number. When no result metric was supplied, use a truthful, restrained qualitative outcome. If evidence is insufficient, state the transferable evidence conservatively instead of inventing a story. End each criterion with one of four approaches—A value alignment, B next action/willingness, C transferable capability, D personal work style—and never use the same approach more than once in this generation. After the submission-ready text, add exactly one machine-readable line: <!-- GENERATION_META {\"used_experiences\":[\"evidence-id\"],\"closing_styles\":[\"A\"]} -->. Every evidence ID must exist in MATCHED RESUME EVIDENCE.",
        "ats_analysis": "List key JD keywords as Covered, Missing, or Evidence needed, with a transparent qualitative match assessment.",
    }.get(document_type)
    if not task:
        raise ValueError("Unsupported document_type")
    task += (
        " FACT BOUNDARY: Do not say an experience strengthened, developed or deepened a skill or understanding. "
        "Do not describe the applicant as highly organised, proactive, adaptable, effective under pressure, "
        "experienced in complex settings or able to deliver outcomes unless the applicant evidence explicitly "
        "states that quality or result. Do not invent an assessment that the applicant's background focused more "
        "or less on an area. Do not infer defence experience from engineering work."
    )
    if document_type == "selection_criteria":
        mode = selection_input_mode(selection_criteria)
        task += (
            f" SELECTION INPUT MODE: {mode}. When the mode is brief user guidance, use the guidance to prioritise only explicit requirements found in the Job Description, create clear requirement-based headings, and expand them into useful responses. Do not invent additional employer criteria. When the mode is full selection criteria, respond separately to every supplied criterion."
            " OVERRIDING RESULT RULE: If the matched evidence has no explicit result, do not add a qualitative outcome, "
            "benefit, deadline, pressure, adaptability or delivery statement; end with the supported action. "
            "Do not turn journals or reconciliation into invoice processing or budget monitoring, and do not turn "
            "task prioritisation into meeting reporting deadlines."
        )
    if correction_instructions.strip():
        task += f" CORRECTION REQUIRED (overrides the prior draft): {correction_instructions.strip()}"
    today = date.today()
    written_date = f"{today.day} {today.strftime('%B %Y')}"
    evidence_pack = resume_evidence_pack(user_experiences_json, resume_plan_json) if document_type == "tailored_resume" else []
    evidence_pack = evidence_pack or matched_evidence_pack(user_experiences_json, evidence_matches_json) or build_evidence_pack(
        master_resume, user_experiences_json, job_description, selection_criteria
    )
    prompt = f"""CURRENT DATE: {written_date}\nTARGET POSITION: {position_title or 'Use the job description'}\nADVERTISED ORGANISATION: {company or 'Use the job description'}\n\nTask: {task}\n\nFor a cover letter, follow the COVER LETTER PLAN as authoritative for priorities, evidence selection and narrative balance. Begin with the written current date exactly as supplied above, never a placeholder. Use the target position and advertised organisation exactly. Do not infer a recruiter/client relationship from wording, industry or company type. Mention such a relationship only when the Job Description explicitly states it, and do not speculate beyond that statement.\n\nUse the APPLICANT PROFILE contact details exactly when producing a resume or cover letter. They override any older contact details in the Master Resume.\n\nAPPLICANT PROFILE:\n{applicant_profile or 'Not provided'}\n\nRESUME CURATION PLAN (deterministic selection, order and compression):\n{resume_plan_json}\n\nCOVER LETTER PLAN (deterministic priorities, selected evidence and structure):\n{cover_letter_plan_json}\n\nDETERMINISTIC SELECTION PLAN (word budgets and evidence statuses are authoritative):\n{selection_plan_json}\n\nBATCH EVIDENCE MATCHES:\n{evidence_matches_json}\n\nMATCHED RESUME EVIDENCE (the only factual source for Cover Letter and Selection Criteria):\n{json.dumps(evidence_pack, ensure_ascii=False)}\n\nEVIDENCE IDS ALREADY DETAILED IN SELECTION CRITERIA:\n{used_experiences}\n\nCLOSING APPROACHES ALREADY USED:\n{used_closing_styles}\n\nMASTER RESUME (context only; do not introduce claims outside the matched evidence for Cover Letter or Selection Criteria):\n{master_resume}\n\nSHARED JOB MODEL (parsed employer requirements and limits; never applicant evidence):\n{structured_job_model}\n\nJOB DESCRIPTION AND ORGANISATION MISSION/VALUES (requirements only, never applicant evidence):\n{job_description}\n\nSELECTION CRITERIA:\n{selection_criteria or 'Not provided'}"""

    if provider == "deepseek":
        try:
            return _finalise_date(_deepseek_draft(prompt))
        except OpenAIError as error:
            raise AIServiceError("DeepSeek could not generate a draft. Check its API key, balance and network connection, then try again.") from error

    try:
        return _finalise_date(_openai_draft(f"{safety_instruction()}\n\n{prompt}"))
    except RuntimeError:
        if settings.ai_fallback_to_deepseek and settings.deepseek_api_key:
            return _finalise_date(_deepseek_draft(prompt))
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Add it to backend/.env, or configure "
            "DEEPSEEK_API_KEY and enable AI_FALLBACK_TO_DEEPSEEK."
        )
    except OpenAIError as error:
        if settings.ai_fallback_to_deepseek and settings.deepseek_api_key:
            try:
                return _finalise_date(_deepseek_draft(prompt))
            except (OpenAIError, AIServiceError) as fallback_error:
                raise AIServiceError(
                    "Both OpenAI and DeepSeek failed. Check both API keys, balances and the network connection."
                ) from fallback_error
        raise AIServiceError(
            "OpenAI could not generate a draft. Check its API key, billing and network connection, then try again."
        ) from error
