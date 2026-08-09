import unittest
from types import SimpleNamespace

from app.applicant_profile import APPLICANT_PROFILE_SCHEMA_VERSION, applicant_profile_prompt


def profile(**overrides):
    values = {
        "title": None, "first_name": "Alex", "last_name": "Morgan",
        "phone": "0400000000", "email": "alex@example.com",
        "postal_address": None, "suburb": None, "state": "WA",
        "postcode": None, "country": "Australia", "work_rights": "not_specified",
        "availability_notice": "not_specified", "target_direction": None,
        "motivation": None, "writing_tone": "natural_professional",
        "preferences_notes": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ApplicantProfileContractTests(unittest.TestCase):
    def test_declared_intent_is_explicitly_separated_from_evidence(self):
        applicant = profile(
            target_direction="Government project roles",
            motivation="I want to contribute to accessible public services.",
            writing_tone="concise_direct",
            preferences_notes="Keep the cover letter to one page.",
        )

        prompt = applicant_profile_prompt(applicant)

        self.assertEqual(APPLICANT_PROFILE_SCHEMA_VERSION, "1.0")
        self.assertIn("APPLICANT_PROFILE_SCHEMA_v1.0", prompt)
        self.assertIn("USER-DECLARED INTENT", prompt)
        self.assertIn("not employment evidence", prompt)
        self.assertIn(applicant.motivation, prompt)
        self.assertIn("concise direct", prompt)

    def test_missing_intent_remains_not_provided(self):
        applicant = profile()

        prompt = applicant_profile_prompt(applicant)

        self.assertIn("Target direction: Not provided", prompt)
        self.assertIn("Motivation: Not provided", prompt)


if __name__ == "__main__":
    unittest.main()
