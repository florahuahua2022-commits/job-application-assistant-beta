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
    }.get(profile.availability_notice, "Do not state availability")
    return "\n".join([
        f"APPLICANT_PROFILE_SCHEMA_v{APPLICANT_PROFILE_SCHEMA_VERSION}",
        "IDENTITY AND DECLARED DETAILS (use exactly where relevant):",
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
