GOVERNMENT_WRITING_RULES_VERSION = "1.1"


def government_writing_rules(english_variant: str = "Australian English") -> str:
    variant = english_variant.strip() or "Australian English"
    return f"""GOVERNMENT_WRITING_RULES_v{GOVERNMENT_WRITING_RULES_VERSION}:
- Use natural, professional {variant} spelling and terminology.
- Prefer active voice, specific evidence and plain language.
- Avoid exaggerated adjectives, cliches and generic AI-sounding phrases.
- Every factual claim must be traceable to supplied CKB source_text; never invent or alter employers, roles, dates, actions, achievements, motivations or figures.
- Preserve the evidence's responsibility level: assisted is not prepared or delivered, supported is not managed or led, liaised is not took responsibility, and participated is not owned.
- Do not add adaptability, accuracy, timeliness, success or delivery outcomes unless the source_text states that quality or result.
- Do not use evaluative claims such as proven capability or strong record unless supplied evidence directly supports the evaluation.
- A neutral application statement is allowed, but personal motivation, attraction, enthusiasm, values alignment and career goals require an explicit confirmed applicant declaration.
- Treat every duty, system and requirement in the Job Description as an employer requirement, not as evidence that the applicant has performed the work.
- Never imply direct experience when the evidence is only transferable; state material evidence gaps plainly.
- Do not copy Job Description or criterion wording verbatim; paraphrase while preserving meaning.
- Prefer quantified outcomes only when the supporting source_text contains the number.
- Never open like an email reply and never use RE: or Subject: as a heading.
- Do not introduce qualifications, systems, security clearances, licences, responsibilities or outcomes absent from the supplied evidence."""
