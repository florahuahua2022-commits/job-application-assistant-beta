from typing import Any
import re


APPLICANT_PROFILE_SCHEMA_VERSION = "1.0"

AVAILABILITY_WORDING = {
    "not_specified": "Do not state availability",
    "immediate": "Available immediately",
    "two_weeks": "Available following two weeks' notice",
    "one_month": "Available following one month's notice",
    "negotiable": "Start date negotiable",
}


def confirmed_availability_wording(value: str) -> str:
    return AVAILABILITY_WORDING.get(value, AVAILABILITY_WORDING["not_specified"])


def availability_claims(content: str) -> list[dict]:
    # ponytail: sentence-level declaration rules; extend with reviewed paraphrases,
    # keeping employment dates and historical project start dates out of this check.
    claims = []
    for match in re.finditer(r"[^\n.!?]+(?:[.!?]|$)", content):
        sentence = match.group().strip()
        text = sentence.casefold().replace("’", "'")
        if not re.search(r"(?:^(?:[-*]\s*)?available\b|\b(?:will|would) be available\b|\bi(?: am|'m)\s+(?:not\s+)?available\b|\bmy availability\b|\bavailability\s*[:=]|\bimmediate availability\b|\b(?:my |a )?notice period\b|\b(?:my )?start date\b|\bi (?:can|could|will|am able to) (?:start|commence|join)\b|\b(?:two|2) weeks?'? notice\b|\b(?:one|1|a) month'?s? notice\b)", text):
            continue
        if re.search(r"\bavailable (?:upon request|for (?:an? )?(?:interview|discussion)|to discuss)\b", text):
            continue
        variants = {
            "immediate": r"\b(?:immediate(?:ly)?|promptly|right away|as soon as possible|without (?:a )?notice)\b",
            "two_weeks": r"\b(?:(?:two|2)[ -]weeks?|a fortnight|14 days)\b",
            "one_month": r"\b(?:one|1|a)[ -]month\b",
            "negotiable": r"\b(?:negotiable|by (?:mutual )?agreement|flexible)\b",
        }
        detected = [key for key, pattern in variants.items() if re.search(pattern, text)]
        value = detected[0] if len(detected) == 1 else "unspecified_claim"
        if re.search(r"\bnot\b|\b(?:on|from|by)\s+\d{1,2}\s+[a-z]+\s+20\d{2}\b|\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", text):
            value = "unspecified_claim"
        claims.append({"location": sentence, "value": value, "start": match.start(), "end": match.end()})
    return claims


def availability_issues(content: str, value: str = "not_specified") -> list[dict]:
    return [{
        "type": "unsupported_availability_claim" if value == "not_specified" else "availability_conflict",
        "severity": "major", "blocks_release": True, "owner": "update_profile_or_system_rewrite",
        "description": "The document promises a start date or notice period that is not confirmed in your profile.",
        "location": claim["location"], "evidence": confirmed_availability_wording(value),
        "recommended_action": "Update your availability in Profile or remove this promise before applying.",
    } for claim in availability_claims(content) if value == "not_specified" or claim["value"] != value]


def polish_availability(content: str, value: str = "not_specified") -> str:
    claims = availability_claims(content)
    for claim in reversed(claims):
        wording = confirmed_availability_wording(value)
        replacement = "" if value == "not_specified" else ("My start date is negotiable." if value == "negotiable" else "I am " + wording[0].lower() + wording[1:] + ".")
        content = content[:claim["start"]] + replacement + content[claim["end"]:]
    return content


def profile_availability_from_prompt(prompt: str | None) -> str:
    return next((key for key, wording in AVAILABILITY_WORDING.items()
                 if f"Confirmed availability wording: {wording}" in (prompt or "")), "not_specified")


def applicant_profile_prompt(profile: Any) -> str:
    address = ", ".join(filter(None, [
        profile.postal_address, profile.suburb, profile.state, profile.postcode, profile.country,
    ]))
    availability = confirmed_availability_wording(profile.availability_notice)
    return "\n".join([
        f"APPLICANT_PROFILE_SCHEMA_v{APPLICANT_PROFILE_SCHEMA_VERSION}",
        "IDENTITY AND DECLARED DETAILS (use exactly where relevant):",
        "Use the confirmed first and last name below in every heading and signature. Do not substitute a preferred name or an older resume identity.",
        f"Name: {' '.join(filter(None, [profile.title, profile.first_name, profile.last_name]))}",
        f"Phone: {profile.phone}",
        f"Email: {profile.email}",
        f"Address: {address}",
        f"Work rights: {profile.work_rights.replace('_', ' ')}",
        f"Confirmed availability wording: {availability}",
        "USER-DECLARED INTENT (not employment evidence; never present these statements as past experience):",
        f"Target direction: {profile.target_direction or 'Not provided'}",
        f"Motivation: {profile.motivation or 'Not provided'}",
        f"Writing tone: {profile.writing_tone.replace('_', ' ')}",
        f"Other preferences: {profile.preferences_notes or 'Not provided'}",
    ])
