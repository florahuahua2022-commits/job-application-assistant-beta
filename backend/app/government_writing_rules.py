GOVERNMENT_WRITING_RULES_VERSION = "1.0"


def government_writing_rules(english_variant: str = "Australian English") -> str:
    variant = english_variant.strip() or "Australian English"
    return f"""GOVERNMENT_WRITING_RULES_v{GOVERNMENT_WRITING_RULES_VERSION}:
- Use natural, professional {variant} spelling and terminology.
- Prefer active voice, specific evidence and plain language.
- Avoid exaggerated adjectives, cliches and generic AI-sounding phrases.
- Every factual claim must be traceable to supplied CKB source_text; never invent or alter employers, roles, dates, actions, achievements, motivations or figures.
- Personal declarations must preserve the Applicant Profile's exact level of specificity. If it says "permanent resident" without a country, never add a country name.
- Never describe a role as current, present or ongoing unless CKB source_text explicitly says current/present/ongoing or supplies an open-ended date range.
- Never add policies, procedures, frameworks, government requirements or recordkeeping requirements unless those terms are explicitly supported by CKB source_text.
- Preserve responsibility level exactly. Evidence that says assisted, supported, contributed or liaised must not become managed, led, owned, directed, coordinated or delivered unless CKB source_text independently supports the stronger verb.
- Maintaining confidential documents supports only that stated action; it does not by itself support claims of discretion, judgement, trustworthiness or handling sensitive matters.
- Treat every duty, system and requirement in the Job Description as an employer requirement, not as evidence that the applicant has performed the work.
- Never imply direct experience when the evidence is only transferable. Describe the supported transferable evidence accurately and positively without opening with a first-person deficit such as "although I have not", "while I have not", "despite not having", "I lack" or "I do not have direct experience".
- Do not hide or fabricate an evidence gap to avoid negative wording. If the employer explicitly requires disclosure of a qualification, experience or limitation, answer neutrally, briefly and factually at the exact level supported by the evidence.
- Do not copy Job Description or criterion wording verbatim; paraphrase while preserving meaning.
- Prefer quantified outcomes only when the supporting source_text contains the number.
- In a formal cover letter, use `RE: [POSITION TITLE]` after the greeting; do not use an email-style Subject line.
- Do not introduce qualifications, systems, security clearances, licences, responsibilities or outcomes absent from the supplied evidence."""
