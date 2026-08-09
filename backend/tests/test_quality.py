import unittest

from app.quality import find_writing_quality_issues


class WritingQualityTests(unittest.TestCase):
    def test_finds_repeated_word_with_line_number(self):
        issues = find_writing_quality_issues("Professional Summary\nI coordinated the the monthly report.")
        self.assertTrue(any(code == "repeated_word" and "line 2" in message for code, message in issues))

    def test_finds_spacing_and_repeated_punctuation(self):
        issues = find_writing_quality_issues("I prepared reports , briefings and registers!!")
        codes = {code for code, _ in issues}
        self.assertIn("punctuation_spacing", codes)
        self.assertIn("repeated_punctuation", codes)

    def test_finds_unbalanced_brackets(self):
        issues = find_writing_quality_issues("Managed reporting (including monthly dashboards.")
        self.assertIn("unbalanced_brackets", {code for code, _ in issues})

    def test_clean_application_text_has_no_warning(self):
        content = "Professional Summary\n\nExperienced project coordinator with clear reporting skills.\n\n- Prepared monthly reports."
        self.assertEqual(find_writing_quality_issues(content), [])


if __name__ == "__main__":
    unittest.main()
