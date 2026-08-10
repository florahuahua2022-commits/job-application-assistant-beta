import unittest
from types import SimpleNamespace

from app.applicant_profile import APPLICANT_PROFILE_SCHEMA_VERSION, applicant_profile_prompt


def profile(**overrides):
    values = {
        "title": None, "first_name": "Alex", "last_name": "Morgan",
        "phone": "0400000000", "email": "alex@example.com",
        "postal_address": None, "suburb": None, "state": "WA",
        "postcode": None, "country": "Australia", "work_rights": "not_specified",
        "work_rights_confirmed": False, "availability_notice": "not_specified",
        "availability_confirmed": False, "target_direction": None,
        "motivation": None, "motivation_confirmed": False, "writing_tone": "natural_professional",
        "preferences_notes": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ApplicantProfileContractTests(unittest.TestCase):
    def test_unspecified_work_rights_are_safe_by_default(self):
        applicant = profile(work_rights="not_specified")

        self.assertEqual(applicant.work_rights, "not_specified")
        self.assertIn("Confirmed work-rights wording: Do not state work rights", applicant_profile_prompt(applicant))

    def test_declared_intent_is_explicitly_separated_from_evidence(self):
        applicant = profile(
            target_direction="Government project roles",
            motivation="I want to contribute to accessible public services.",
            motivation_confirmed=True,
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

    def test_unconfirmed_sensitive_declarations_are_omitted(self):
        applicant = profile(
            work_rights="permanent_resident",
            availability_notice="one_month",
            motivation="I want this role.",
        )

        prompt = applicant_profile_prompt(applicant)

        self.assertIn("Do not state work rights", prompt)
        self.assertIn("Do not state availability", prompt)
        self.assertIn("Motivation: Not provided", prompt)
        self.assertNotIn("permanent resident", prompt.lower())
        self.assertNotIn("one month's notice", prompt.lower())


if __name__ == "__main__":
    unittest.main()
