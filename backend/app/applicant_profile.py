from typing import Any


APPLICANT_PROFILE_SCHEMA_VERSION = "1.0"


def applicant_profile_prompt(profile: Any) -> str:
    address = ", ".join(filter(None, [
        profile.postal_address, profile.suburb, profile.state, profile.postcode, profile.country,
    ]))
    availability = {
        "two_weeks": "Available following two weeks' notice",
        "one_month": "Available following one month's notice",
        "negotiable": "Start date negotiable",
        "not_specified": "Do not state availability",
    }.get(profile.availability_notice, "Do not state availability") if profile.availability_confirmed else "Do not state availability"
    work_rights = {
        "citizen": "Australian citizen",
        "permanent_resident": "Australian permanent resident",
        "visa": "Visa holder",
        "not_specified": "Do not state work rights",
    }.get(profile.work_rights, "Do not state work rights") if profile.work_rights_confirmed else "Do not state work rights"
    return "\n".join([
        f"APPLICANT_PROFILE_SCHEMA_v{APPLICANT_PROFILE_SCHEMA_VERSION}",
        "IDENTITY AND DECLARED DETAILS (use exactly where relevant):",
        f"Name: {' '.join(filter(None, [profile.title, profile.first_name, profile.last_name]))}",
        f"Phone: {profile.phone}",
        f"Email: {profile.email}",
        f"Address: {address}",
        f"Confirmed work-rights wording: {work_rights}",
        f"Confirmed availability wording: {availability}",
        "USER-DECLARED INTENT (not employment evidence; never present these statements as past experience):",
        f"Target direction: {profile.target_direction or 'Not provided'}",
        f"Motivation: {profile.motivation if profile.motivation_confirmed and profile.motivation else 'Not provided'}",
        f"Writing tone: {profile.writing_tone.replace('_', ' ')}",
        f"Other preferences: {profile.preferences_notes or 'Not provided'}",
    ])
