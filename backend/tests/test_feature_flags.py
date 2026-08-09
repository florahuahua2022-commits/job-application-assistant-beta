import unittest

from app.feature_flags import GENERATION_FEATURES, generation_feature_status


class GenerationFeatureFlagTests(unittest.TestCase):
    def test_every_generation_type_has_an_independent_default_on_flag(self):
        self.assertEqual(
            set(GENERATION_FEATURES),
            {"tailored_resume", "cover_letter", "selection_criteria", "ats_analysis"},
        )
        flags = {setting: True for setting in GENERATION_FEATURES.values()}
        self.assertTrue(all(generation_feature_status(kind, flags)["enabled"] for kind in GENERATION_FEATURES))

    def test_one_pipeline_can_be_disabled_without_disabling_others(self):
        flags = {setting: True for setting in GENERATION_FEATURES.values()}
        flags["enable_selection_criteria"] = False

        self.assertFalse(generation_feature_status("selection_criteria", flags)["enabled"])
        self.assertTrue(generation_feature_status("cover_letter", flags)["enabled"])
        self.assertTrue(generation_feature_status("tailored_resume", flags)["enabled"])

    def test_unknown_document_type_is_not_supported(self):
        status = generation_feature_status("interview_script", {})

        self.assertFalse(status["supported"])
        self.assertFalse(status["enabled"])


if __name__ == "__main__":
    unittest.main()
