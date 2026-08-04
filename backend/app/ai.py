from datetime import date
import re

from openai import OpenAI, OpenAIError
from .config import settings

def target_english_variant() -> str:
    return settings.target_english_variant.strip() or "Australian English"


def safety_instruction() -> str:
    variant = target_english_variant()
    return f"""You are a careful Australian job-application writer. Use natural, professional {variant}. Only use facts found in the supplied Master Resume and Applicant Profile. Never invent employment, qualifications, metrics, systems, security clearances, licences, responsibilities or achievements. Treat every duty, system and requirement in the Job Description as an employer requirement, not as evidence that the applicant has done it. Never imply direct experience when the evidence is only transferable. Never compare the value, scale, complexity or significance of two projects unless both sources provide explicit, verifiable facts supporting that comparison. When a named system is absent from the evidence, do not claim proficiency, comfort, fast learning or quick adaptation; if useful, state the gap plainly and refer only to genuinely analogous tools or processes. Avoid subjective suitability claims and generic AI-style wording such as 'I am confident', 'I am excited', 'I am well placed', 'I am comfortable learning', 'I can adapt quickly', 'I am writing to express my interest', 'proven track record', 'dynamic professional', 'passionate about', 'leverage my skills', or 'well placed'. Let verified examples demonstrate suitability. Prefer specific evidence and plain language. Never calculate or invent a calendar start date from a notice period; use the exact confirmed availability wording supplied in the Applicant Profile. Never use American spelling when the configured English variant uses a different standard spelling."""


class AIServiceError(Exception):
    """A safe, user-facing failure when the AI provider cannot generate a draft."""


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
) -> str:
    provider = settings.ai_provider.strip().lower()
    if provider not in {"openai", "deepseek"}:
        raise ValueError("AI_PROVIDER must be either 'openai' or 'deepseek'.")
    task = {
        "tailored_resume": "Create a clean ATS-friendly resume of about 550-750 words. Use a contact header, short professional summary, relevant capabilities, and reverse-chronological employment history. Use concise evidence-based bullets, present tense for the current role and past tense for earlier roles. Keep original employers, titles and dates truthful. Never add metrics or achievements that are not in the evidence. Do not use tables, columns, graphics, first-person pronouns, selection criteria, reviewer notes or match scores.",
        "cover_letter": "Write a concise one-page cover letter of about 300-450 words in a natural Australian professional voice. Begin with the supplied written date, then an appropriate salutation. Name the exact role and advertised organisation. Never infer that the advertised organisation is a recruiter, agency, intermediary or representative of a client unless the Job Description explicitly states that relationship. If an explicit recruiter/client relationship is present, describe only what the Job Description confirms. Use 3-5 short body paragraphs built around specific relevant evidence, not a summary of the whole resume. Do not copy duties from the Job Description into the applicant's experience. Do not describe earlier projects as comparable in value, scale or complexity without explicit evidence. If the Job Description names software absent from the Master Resume, acknowledge the gap briefly only when material and describe only verified transferable systems experience; do not say the applicant is comfortable learning, can adapt quickly or will learn quickly. Do not use subjective suitability conclusions such as 'I am confident', 'I am well placed' or similar paraphrases. Let the evidence stand on its own. Use the confirmed availability wording exactly and never convert a notice period into an estimated date. Salutation and sign-off must match: use 'Yours sincerely' only when addressing a named person, and use 'Yours faithfully' after a generic salutation such as 'Dear Hiring Manager'. Output only the submission-ready letter.",
        "selection_criteria": "Respond separately to every supplied criterion. Use one concrete, truthful example for each criterion and make the Situation, Action and Result evident in natural prose without mechanically printing STAR labels. State the applicant's personal actions clearly, distinguish team outcomes from personal contribution, and include a genuine result only when supported by the Master Resume. If evidence is incomplete, frame transferable evidence honestly rather than inventing details. Output only the submission-ready responses.",
        "ats_analysis": "List key JD keywords as Covered, Missing, or Evidence needed, with a transparent qualitative match assessment.",
    }.get(document_type)
    if not task:
        raise ValueError("Unsupported document_type")
    today = date.today()
    written_date = f"{today.day} {today.strftime('%B %Y')}"
    prompt = f"""CURRENT DATE: {written_date}\nTARGET POSITION: {position_title or 'Use the job description'}\nADVERTISED ORGANISATION: {company or 'Use the job description'}\n\nTask: {task}\n\nFor a cover letter, begin with the written current date exactly as supplied above, never a placeholder. Use the target position and advertised organisation exactly. Do not infer a recruiter/client relationship from wording, industry or company type. Mention such a relationship only when the Job Description explicitly states it, and do not speculate beyond that statement.\n\nUse the APPLICANT PROFILE contact details exactly when producing a resume or cover letter. They override any older contact details in the Master Resume.\n\nAPPLICANT PROFILE:\n{applicant_profile or 'Not provided'}\n\nMASTER RESUME:\n{master_resume}\n\nJOB DESCRIPTION:\n{job_description}\n\nSELECTION CRITERIA:\n{selection_criteria or 'Not provided'}"""

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
