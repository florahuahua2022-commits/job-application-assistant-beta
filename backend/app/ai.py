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

Only use facts found in the supplied Master Resume, CKB and Applicant Profile. Never compare the value, scale, complexity or significance of two projects unless both sources provide explicit, verifiable facts supporting that comparison. When a named system is absent from the evidence, do not claim proficiency, comfort, fast learning or quick adaptation; if useful, state the gap plainly and refer only to genuinely analogous tools or processes. Avoid subjective suitability claims such as 'I am confident', 'I am excited', 'I am well placed', 'I am comfortable learning', 'I can adapt quickly', 'I am writing to express my interest', 'proven track record', 'dynamic professional', 'passionate about', or 'leverage my skills'. Never calculate or invent a calendar start date from a notice period; use the exact confirmed availability wording supplied in the Applicant Profile. Never use American spelling when the configured English variant uses a different standard spelling."""


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
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.create(model=settings.openai_model, input=prompt)
    return response.output_text


def _deepseek_draft(prompt: str) -> str:
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured.")
    client = OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.ai_request_timeout_seconds,
        max_retries=1,
    )
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
    content = response.choices[0].message.content
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
            raw = _deepseek_draft(prompt)
        elif provider == "openai":
            try:
                raw = _openai_draft(prompt)
            except OpenAIError:
                if not settings.ai_fallback_to_deepseek:
                    raise
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


def generate_selection_criteria_bundle(ckb_json: str, selection_plan_json: str) -> dict:
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
    generator_retries = 0
    for plan_item in plan["items"]:
        matched = [evidence_by_id[value] for value in plan_item.get("matched_evidence") or [] if value in evidence_by_id]
        base_prompt = f"""You are writing one Selection Criterion response for an Australian government application.

{government_writing_rules(target_english_variant())}

If evidence is transferable or weak, frame it conservatively. If evidence is insufficient, do not fabricate a story.

CRITERION PLAN:
{json.dumps(plan_item, ensure_ascii=False)}

MATCHED CKB EVIDENCE (the only factual source):
{json.dumps(matched, ensure_ascii=False)}

Write about {plan_item.get('allocated_word_limit')} words. Return JSON only:
{{"criteria_id":"{plan_item.get('criteria_id')}","evidence_used":["EV..."],"star":{{"situation":"...","task":"...","action":"...","result":"..."}},"final_response":"natural paragraph text","word_count":0}}

Every evidence_used ID must appear in CRITERION PLAN matched_evidence. Include only IDs materially used in final_response. The STAR fields are audit fields; do not print S/T/A/R labels inside final_response."""
        validation = None
        response: dict = {}
        for attempt in range(2):
            retry_note = ""
            if validation and validation["issues"]:
                retry_note = "\n\nYour previous output failed deterministic validation. Correct only these issues:\n" + json.dumps(validation["issues"], ensure_ascii=False)
            try:
                response = _json_object(_selection_provider_response(base_prompt + retry_note))
            except (OpenAIError, ValueError) as error:
                if attempt == 1:
                    raise AIServiceError(f"Criterion {plan_item.get('criteria_id')} could not be generated as valid JSON.") from error
                validation = {"issues": [{"code": "invalid_json", "message": "Return one valid JSON object only."}]}
                continue
            if str(response.get("criteria_id") or "") != str(plan_item.get("criteria_id")):
                validation = {"issues": [{"code": "criteria_mismatch", "message": "Return the exact criteria_id from the plan."}]}
            else:
                validation = hard_validate_response(response, plan_item)
            if validation.get("valid"):
                break
        if not validation or not validation.get("valid"):
            issue = (validation or {}).get("issues", [{}])[0].get("message", "Unknown validation error")
            raise AIServiceError(f"Criterion {plan_item.get('criteria_id')} failed validation: {issue}")
        generator_retries += attempt
        response["word_count"] = validation["actual_word_count"]
        response["validation"] = validation
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
        "telemetry": {"generator_retries": generator_retries, "criterion_count": len(responses)},
    }


def review_selection_criteria_batch(ckb_json: str, selection_plan_json: str, bundle: dict) -> dict:
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
- fabricated_entity
- evidence_mismatch
- internal_inconsistency
- jd_wording_repeated
- ai_tone
- declared_evidence_unused
- unmatched_evidence_used

Do not evaluate exact word counts, JSON structure, evidence reuse percentages or employer share; deterministic application logic already checks them. A stylistic preference alone must not fail a response.

BATCH PACKAGE:
{json.dumps(package, ensure_ascii=False)}

FULL CAREER KNOWLEDGE BASE WITH SOURCE TEXT:
{json.dumps(ckb, ensure_ascii=False)}

Return JSON only:
{{"results":[{{"criteria_id":"...","status":"pass|fail","issues":[{{"type":"unsupported_claim","description":"...","evidence":"source detail","location":"response phrase","recommended_action":"specific guidance"}}],"recommendation":"optional guidance"}}]}}

Return every criteria_id exactly once. Use pass with an empty issues array when no material issue exists."""
    last_error = ""
    for attempt in range(2):
        try:
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


def auto_fix_selection_criteria_bundle(ckb_json: str, selection_plan_json: str, bundle: dict, review: dict) -> dict:
    """Repair only criteria that the factual reviewer failed, without expanding their evidence."""
    ckb = json.loads(ckb_json or "[]")
    plan = json.loads(selection_plan_json or "{}")
    evidence_by_id = {str(item.get("evidence_id")): item for item in ckb if isinstance(item, dict)}
    plan_by_id = {str(item.get("criteria_id")): item for item in plan.get("items") or []}
    issues_by_id = {
        str(item.get("criteria_id")): item.get("issues") or []
        for item in review.get("results") or [] if item.get("status") == "fail"
    }
    repaired_responses = []
    for response in bundle.get("responses") or []:
        criteria_id = str(response.get("criteria_id") or "")
        issues = issues_by_id.get(criteria_id)
        if not issues:
            repaired_responses.append(response)
            continue
        plan_item = plan_by_id.get(criteria_id) or {}
        allowed_ids = [str(value) for value in plan_item.get("matched_evidence") or []]
        matched = [evidence_by_id[value] for value in allowed_ids if value in evidence_by_id]
        prompt = f"""You are repairing one Selection Criterion response after factual validation.

PREVIOUS RESPONSE:
{json.dumps(response, ensure_ascii=False)}

VALIDATOR ERRORS:
{json.dumps(issues, ensure_ascii=False)}

CRITERION PLAN:
{json.dumps(plan_item, ensure_ascii=False)}

MATCHED CKB SOURCE TEXT (the only factual ground truth):
{json.dumps(matched, ensure_ascii=False)}

Return the full corrected response as JSON only, using the same schema as PREVIOUS RESPONSE.
- Delete unsupported outcomes, suitability claims, confidence claims, trust/reputation claims and other subjective conclusions. Do not replace them with a new outcome.
- If a phrase combines separate source facts into a stronger claim, separate or soften it using the source_text's own wording.
- Never treat the criterion or Job Description as evidence of an applicant achievement.
- Do not invent a result merely to complete STAR. If no result is evidenced, set star.result to an empty string and end final_response with the last supported action or responsibility.
- Use only evidence IDs listed in CRITERION PLAN matched_evidence, and include only IDs actually used.
- Do not add any fact, tool, duration, outcome or positive judgement absent from source_text.
- Avoid phrases such as 'I am confident', 'prepared me well', 'earned trust', and 'successfully' unless the exact claim is supported.
- Keep the response natural after deletion and return the exact criteria_id {criteria_id}."""
        fixed = _json_object(_selection_provider_response(prompt))
        if str(fixed.get("criteria_id") or "") != criteria_id:
            raise AIServiceError(f"Criterion {criteria_id} repair returned the wrong criteria_id.")
        validation = hard_validate_response(fixed, plan_item)
        if not validation.get("valid"):
            raise AIServiceError(f"Criterion {criteria_id} repair failed deterministic validation.")
        fixed["word_count"] = validation["actual_word_count"]
        fixed["validation"] = validation
        repaired_responses.append(fixed)
    used_ids = []
    for response in repaired_responses:
        for evidence_id in response.get("evidence_used") or []:
            if evidence_id not in used_ids:
                used_ids.append(evidence_id)
    content = "\n\n".join(
        f"## {plan_by_id[str(response.get('criteria_id'))]['criteria_text']}\n\n{response['final_response'].strip()}"
        for response in repaired_responses
    )
    return {**bundle, "content": content, "responses": repaired_responses, "used_experiences": used_ids,
            "actual_total_word_count": sum(item.get("word_count", 0) for item in repaired_responses)}


def repair_selection_criteria_bundle(ckb_json: str, selection_plan_json: str, bundle: dict, max_rounds: int = 2) -> tuple[dict, dict]:
    """Run a bounded reviewer/repair loop and expose a user-friendly terminal status."""
    current = bundle
    total_repairs = 0
    for _ in range(max_rounds):
        review = review_selection_criteria_batch(ckb_json, selection_plan_json, current)
        if review.get("status") == "pass":
            review["generation_status"] = "clean"
            review.setdefault("telemetry", {})["repair_rounds"] = total_repairs
            return current, review
        current = auto_fix_selection_criteria_bundle(ckb_json, selection_plan_json, current, review)
        total_repairs += 1
    final_review = review_selection_criteria_batch(ckb_json, selection_plan_json, current)
    final_review["generation_status"] = "clean" if final_review.get("status") == "pass" else "needs_ckb_update"
    final_review["remaining_issues"] = [
        issue for item in final_review.get("results") or [] for issue in item.get("issues") or []
    ]
    final_review.setdefault("telemetry", {})["repair_rounds"] = total_repairs
    return current, final_review


def review_cover_letter(
    ckb_json: str,
    job_model_json: str,
    cover_letter_plan_json: str,
    applicant_profile: str | None,
    content: str,
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

The Applicant Profile intent may support motivation or values alignment, but it is not employment evidence. A style_only preference must never cause failure by itself. Do not calculate exact word counts; application logic handles mechanical constraints.

Classify a named organisation, program, project, framework, policy, system or initiative as fabricated_entity when it does not appear in the source assigned to that kind of fact. Applicant experience entities must appear in CKB source_text; advertised-role or organisation entities must appear in the Shared Job Model. Do not require an advertised-role entity to appear in the CKB, and do not treat the Job Description as evidence that the applicant worked with that entity.

SHARED JOB MODEL:
{json.dumps(job_model, ensure_ascii=False)}

COVER LETTER PLAN:
{json.dumps(plan, ensure_ascii=False)}

APPLICANT PROFILE DECLARATIONS:
{applicant_profile or 'Not provided'}

FULL CKB WITH SOURCE TEXT:
{json.dumps(ckb, ensure_ascii=False)}

FINAL COVER LETTER:
{content}

Return JSON only:
{{"status":"pass|fail","issues":[{{"type":"unsupported_claim","description":"...","evidence":"source detail","location":"letter phrase","recommended_action":"specific guidance"}}],"recommendation":"optional guidance"}}

Use pass with an empty issues array when there is no material issue."""
    last_error = ""
    for attempt in range(2):
        try:
            raw = _json_object(_selection_provider_response(prompt + (f"\n\nPrevious validation error: {last_error}" if last_error else "")))
            result = normalise_document_review(raw, "cover_letter")
            result["telemetry"] = {"reviewer_retries": attempt}
            return result
        except (OpenAIError, ValueError) as error:
            last_error = str(error)
    raise AIServiceError(f"Cover Letter Reviewer failed validation: {last_error or 'unknown error'}")


def auto_fix_cover_letter(
    content: str,
    review: dict,
    ckb_json: str,
    job_model_json: str,
    cover_letter_plan_json: str,
    applicant_profile: str | None,
) -> str:
    issues = [
        issue for result in review.get("results") or [] for issue in result.get("issues") or []
        if issue.get("severity") != "advisory" and issue.get("type") != "style_only"
    ]
    if not issues:
        return content
    prompt = f"""You are correcting a Cover Letter after factual validation. Return the full corrected letter only.

PREVIOUS COVER LETTER:
---
{content}
---

VALIDATOR ERRORS:
{json.dumps(issues, ensure_ascii=False)}

CKB SOURCE_TEXT (the only ground truth for the applicant's employment, skills, achievements, dates and tools):
{ckb_json}

APPLICANT PROFILE DECLARATIONS (ground truth only for personal declarations; preserve its exact level of specificity):
{applicant_profile or 'Not provided'}

SHARED JOB MODEL (ground truth for the advertised role and organisation only; never applicant evidence):
{job_model_json}

COVER LETTER PLAN:
{cover_letter_plan_json}

Repair every validator error using these rules:
- Delete a claim that has no support. Do not replace it with another unverified claim.
- Delete any fabricated_entity (organisation, program, project, framework, policy, system or initiative) that is absent from its permitted source. Do not replace it with a guessed generic or another entity.
- Soften an overstated claim to the CKB source_text's own wording and responsibility level (for example, "assisted in prioritising" must not become "managed").
- Do not calculate or infer tenure. Remove "over a decade", "10+ years" and similar duration claims unless that exact duration appears in source_text.
- Job or organisation facts may come from the Shared Job Model, but describe them as advertised role facts, never as the applicant's experience.
- Personal declarations must not become more specific than the Applicant Profile (for example, keep "permanent resident" rather than adding a country).
- Remove subjective suitability, confidence, trust, discretion or success claims unless directly supported by the correct source.
- Paraphrase Job Description wording and never present it as proven experience without independent CKB support.
- Preserve the letter's contact details, position title, salutation, sign-off and natural readability.
- Do not add any new factual claim while repairing.
"""
    return _selection_provider_response(prompt).strip()


def repair_cover_letter(
    content: str,
    ckb_json: str,
    job_model_json: str,
    cover_letter_plan_json: str,
    applicant_profile: str | None,
    max_rounds: int = 2,
) -> tuple[str, dict]:
    current = content
    repair_rounds = 0
    for _ in range(max_rounds):
        review = review_cover_letter(ckb_json, job_model_json, cover_letter_plan_json, applicant_profile, current)
        if review.get("status") == "pass":
            review["generation_status"] = "clean"
            review.setdefault("telemetry", {})["repair_rounds"] = repair_rounds
            return current, review
        current = auto_fix_cover_letter(
            current, review, ckb_json, job_model_json, cover_letter_plan_json, applicant_profile
        )
        repair_rounds += 1
    final_review = review_cover_letter(ckb_json, job_model_json, cover_letter_plan_json, applicant_profile, current)
    final_review["generation_status"] = "clean" if final_review.get("status") == "pass" else "needs_ckb_update"
    final_review["remaining_issues"] = [
        issue for result in final_review.get("results") or [] for issue in result.get("issues") or []
    ]
    final_review.setdefault("telemetry", {})["repair_rounds"] = repair_rounds
    return current, final_review


def review_tailored_resume(
    ckb_json: str,
    job_model_json: str,
    resume_plan_json: str,
    content: str,
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

FINAL TAILORED CV:
{content}

Return JSON only:
{{"status":"pass|fail","issues":[{{"type":"unsupported_claim","description":"...","evidence":"source detail","location":"CV phrase","recommended_action":"specific guidance"}}],"recommendation":"optional guidance"}}

Use pass with an empty issues array when there is no material issue."""
    last_error = ""
    for attempt in range(2):
        try:
            raw = _json_object(_selection_provider_response(prompt + (f"\n\nPrevious validation error: {last_error}" if last_error else "")))
            result = normalise_document_review(raw, "tailored_resume")
            result["telemetry"] = {"reviewer_retries": attempt}
            return result
        except (OpenAIError, ValueError) as error:
            last_error = str(error)
    raise AIServiceError(f"Resume Reviewer failed validation: {last_error or 'unknown error'}")


def classify_resume_review_errors(review: dict) -> list[dict]:
    """Convert material reviewer findings into the two safe repair actions."""
    errors = []
    for result in review.get("results") or []:
        for issue in result.get("issues") or []:
            if issue.get("severity") == "advisory" or issue.get("type") == "style_only":
                continue
            description = str(issue.get("description") or "")
            lowered = description.lower()
            partial_support = issue.get("type") == "unsupported_inference" or any(marker in lowered for marker in (
                "duration", "tenure", "qualifier", "overstated", "stronger than", "upgrade",
            ))
            complete_absence = not partial_support and any(marker in lowered for marker in (
                "not mentioned", "not present", "no evidence", "not in the ckb", "does not appear",
            ))
            errors.append({
                "id": f"err_{len(errors) + 1}",
                "location": str(issue.get("location") or "Tailored CV"),
                "claim": str(issue.get("location") or description),
                "issue": description,
                "fix_type": "remove" if complete_absence else "remove_or_soften",
            })
    return errors


def auto_fix_tailored_resume(content: str, errors: list[dict], ckb_json: str) -> str:
    if not errors:
        return content
    prompt = f"""You are correcting an ATS-friendly tailored CV after factual validation. Return the full corrected CV only.

PREVIOUS CV:
---
{content}
---

UNSUPPORTED CLAIMS:
{json.dumps(errors, ensure_ascii=False)}

CKB SOURCE_TEXT (the only ground truth):
---
{ckb_json}
---

Rules:
- For fix_type "remove", delete the unsupported claim entirely. Do not replace it with another unverified claim.
- For fix_type "remove_or_soften", rewrite using only what CKB source_text actually supports.
- Do not introduce any new claim not present in source_text.
- Preserve truthful names, roles, employers, dates, contact details, Markdown headings and overall CV structure.
- Keep sentences natural after removal and output the full corrected CV, not a diff.
- Do not output commentary, JSON, reviewer notes or GENERATION_META."""
    try:
        fixed = _selection_provider_response(prompt).strip()
    except OpenAIError as error:
        raise AIServiceError(f"Resume automatic correction failed: {error}") from error
    if not fixed:
        raise AIServiceError("Resume automatic correction returned no content.")
    return fixed


def repair_tailored_resume(
    content: str,
    ckb_json: str,
    job_model_json: str,
    resume_plan_json: str,
    max_rounds: int = 2,
) -> tuple[str, dict]:
    """Validate and repair a CV with a strict upper bound on AI correction calls."""
    review: dict = {}
    for _round in range(max_rounds):
        review = review_tailored_resume(ckb_json, job_model_json, resume_plan_json, content)
        errors = classify_resume_review_errors(review)
        if not errors:
            review["generation_status"] = "clean"
            review["remaining_issues"] = []
            return content, review
        content = auto_fix_tailored_resume(content, errors, ckb_json)
    review = review_tailored_resume(ckb_json, job_model_json, resume_plan_json, content)
    errors = classify_resume_review_errors(review)
    review["generation_status"] = "needs_ckb_update" if errors else "clean"
    review["remaining_issues"] = errors
    return content, review


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
) -> str:
    provider = settings.ai_provider.strip().lower()
    if provider not in {"openai", "deepseek"}:
        raise ValueError("AI_PROVIDER must be either 'openai' or 'deepseek'.")
    task = {
        "tailored_resume": "Curate a clean ATS-friendly CV according to the deterministic RESUME CURATION PLAN, targeting about 550-750 words and no more than two pages when exported. Use these exact Markdown section headings in this order: '## Professional Summary', '## Key Skills', and '## Work Experience'. Finish with '## References' followed by 'Available upon request'. Treat the selected CKB evidence as the only source for employment bullets. Turn responsibility into concise action-led bullets and include context only where it clarifies scope. Use a supplied exact result or rough range when available; when result is blank or marked unavailable, write a restrained qualitative outcome and never invent a number. Use present tense for the current role and past tense for earlier roles. Keep original employers, titles and dates truthful. Do not use tables, columns, graphics, first-person pronouns, selection criteria, reviewer notes or match scores. CRITICAL CONSTRAINT: Every factual claim (dates, tenure, tools/software, skills, employer names, policies and frameworks) must be directly traceable to CKB source_text. Never infer duration without explicit dates; never name tools that do not literally appear; never upgrade vague terms into formal ones; never repeat JD wording as a proven skill unless source_text independently supports it; when uncertain, use source_text's own weaker wording. After the submission-ready CV, add exactly one machine-readable line: <!-- GENERATION_META {\"used_experiences\":[\"evidence-id\"],\"closing_styles\":[]} -->. Every evidence ID must exist in RESUME CURATION PLAN selected_evidence.",
        "cover_letter": "Write a concise one-page cover letter of about 300-450 words. Never use 'RE:' or 'Subject:' as a heading; use a natural 'Application for [position]' heading if a heading is helpful. This letter must work as a standalone companion to the CV even when no selection criteria document exists. Select only one or two of the strongest items from MATCHED RESUME EVIDENCE and summarise them briefly instead of retelling the CV. When evidence IDs were detailed in selection criteria, those items may receive at most one brief sentence total; do not repeat their detail or the same skill claims. At least 60% of the body must explain why this role, why this organisation, what the work means to the candidate, and how the candidate's work style and values align with the organisation's mission. Use the supplied organisation mission/values when present. If the JD mentions roster/shift work, medical checks, right to work, police clearance or a licence, add a brief factual confirmation paragraph at the end, confirming only facts present in the evidence. Begin with the supplied date. Use 'Yours sincerely' only for a named addressee; use 'Yours faithfully' after a generic salutation. After the submission-ready letter, add exactly one machine-readable line: <!-- GENERATION_META {\"used_experiences\":[\"evidence-id\"],\"closing_styles\":[]} -->.",
        "selection_criteria": "Respond separately to every supplied criterion. Every response must follow a natural Situation–Task–Action–Result flow without printing S/T/A/R labels. Use only MATCHED RESUME EVIDENCE. Never treat the JD as proof of applicant experience. Use only results supplied by the user; never invent a number. When no result metric was supplied, use a truthful, restrained qualitative outcome. If evidence is insufficient, state the transferable evidence conservatively instead of inventing a story. End each criterion with one of four approaches—A value alignment, B next action/willingness, C transferable capability, D personal work style—and never use the same approach more than once in this generation. After the submission-ready text, add exactly one machine-readable line: <!-- GENERATION_META {\"used_experiences\":[\"evidence-id\"],\"closing_styles\":[\"A\"]} -->. Every evidence ID must exist in MATCHED RESUME EVIDENCE.",
        "ats_analysis": "List key JD keywords as Covered, Missing, or Evidence needed, with a transparent qualitative match assessment.",
    }.get(document_type)
    if not task:
        raise ValueError("Unsupported document_type")
    if document_type == "selection_criteria":
        mode = selection_input_mode(selection_criteria)
        task += (
            f" SELECTION INPUT MODE: {mode}. When the mode is brief user guidance, use the guidance to prioritise only explicit requirements found in the Job Description, create clear requirement-based headings, and expand them into useful responses. Do not invent additional employer criteria. When the mode is full selection criteria, respond separately to every supplied criterion."
        )
    today = date.today()
    written_date = f"{today.day} {today.strftime('%B %Y')}"
    evidence_pack = resume_evidence_pack(user_experiences_json, resume_plan_json) if document_type == "tailored_resume" else []
    evidence_pack = evidence_pack or matched_evidence_pack(user_experiences_json, evidence_matches_json) or build_evidence_pack(
        master_resume, user_experiences_json, job_description, selection_criteria
    )
    prompt = f"""CURRENT DATE: {written_date}\nTARGET POSITION: {position_title or 'Use the job description'}\nADVERTISED ORGANISATION: {company or 'Use the job description'}\n\nTask: {task}\n\nFor a cover letter, follow the COVER LETTER PLAN as authoritative for priorities, evidence selection and narrative balance. Begin with the written current date exactly as supplied above, never a placeholder. Use the target position and advertised organisation exactly. Do not infer a recruiter/client relationship from wording, industry or company type. Mention such a relationship only when the Job Description explicitly states it, and do not speculate beyond that statement. Never invent or import a named organisation, program, project, framework, policy, system or initiative. Applicant-experience entities must appear in MATCHED RESUME EVIDENCE; advertised-role or organisation entities must appear in the SHARED JOB MODEL or supplied Job Description, and must never be presented as applicant experience.\n\nUse the APPLICANT PROFILE contact details exactly when producing a resume or cover letter. They override any older contact details in the Master Resume.\n\nAPPLICANT PROFILE:\n{applicant_profile or 'Not provided'}\n\nRESUME CURATION PLAN (deterministic selection, order and compression):\n{resume_plan_json}\n\nCOVER LETTER PLAN (deterministic priorities, selected evidence and structure):\n{cover_letter_plan_json}\n\nDETERMINISTIC SELECTION PLAN (word budgets and evidence statuses are authoritative):\n{selection_plan_json}\n\nBATCH EVIDENCE MATCHES:\n{evidence_matches_json}\n\nMATCHED RESUME EVIDENCE (the only factual source for Cover Letter and Selection Criteria):\n{json.dumps(evidence_pack, ensure_ascii=False)}\n\nEVIDENCE IDS ALREADY DETAILED IN SELECTION CRITERIA:\n{used_experiences}\n\nCLOSING APPROACHES ALREADY USED:\n{used_closing_styles}\n\nMASTER RESUME (context only; do not introduce claims outside the matched evidence for Cover Letter or Selection Criteria):\n{master_resume}\n\nSHARED JOB MODEL (parsed employer requirements and limits; never applicant evidence):\n{structured_job_model}\n\nJOB DESCRIPTION AND ORGANISATION MISSION/VALUES (requirements only, never applicant evidence):\n{job_description}\n\nSELECTION CRITERIA:\n{selection_criteria or 'Not provided'}"""

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
