GOVERNMENT_WRITING_RULES_VERSION = "1.0"


def government_writing_rules(english_variant: str = "Australian English") -> str:
    variant = english_variant.strip() or "Australian English"
    return f"""GOVERNMENT_WRITING_RULES_v{GOVERNMENT_WRITING_RULES_VERSION}:
- Use natural, professional {variant} spelling and terminology.
- Prefer active voice, specific evidence and plain language.
- Avoid exaggerated adjectives, cliches and generic AI-sounding phrases.
- Every factual claim must be traceable to supplied CKB source_text; never invent or alter employers, roles, dates, actions, achievements, motivations or figures.
- Treat every duty, system and requirement in the Job Description as an employer requirement, not as evidence that the applicant has performed the work.
- Never imply direct experience when the evidence is only transferable; state material evidence gaps plainly.
- Do not copy Job Description or criterion wording verbatim; paraphrase while preserving meaning.
- Prefer quantified outcomes only when the supporting source_text contains the number.
- Never open like an email reply and never use RE: or Subject: as a heading.
- Do not introduce qualifications, systems, security clearances, licences, responsibilities or outcomes absent from the supplied evidence."""
