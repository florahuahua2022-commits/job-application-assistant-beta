import unittest

from app.jd_similarity import find_jd_similarity_issues


class JobDescriptionSimilarityTests(unittest.TestCase):
    def setUp(self):
        self.jd = (
            "Maintain project documentation, contracts and technical records.\n"
            "Demonstrate experience managing competing priorities across multiple concurrent projects."
        )

    def test_dtmi_two_distinct_phrases_are_blocking(self):
        content = (
            "I maintained project documentation, contracts and technical records while "
            "managing competing priorities across multiple concurrent projects."
        )

        issues = find_jd_similarity_issues(content, self.jd)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "error")
        self.assertEqual(issues[0]["code"], "jd_wording_repeated")
        self.assertGreaterEqual(issues[0]["cumulative_match_words"], 12)
        self.assertEqual(len(issues[0]["matches"]), 2)

    def test_one_six_to_nine_word_phrase_is_warning(self):
        issues = find_jd_similarity_issues(
            "I maintained project documentation, contracts and technical records.",
            self.jd,
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "warning")
        self.assertEqual(issues[0]["longest_match_words"], 6)

    def test_ten_contiguous_words_are_blocking(self):
        jd = "Coordinate the preparation of detailed monthly reports for multiple active infrastructure projects."
        issues = find_jd_similarity_issues(jd, jd)

        self.assertEqual(issues[0]["severity"], "error")
        self.assertGreaterEqual(issues[0]["longest_match_words"], 10)

    def test_long_sentence_with_small_edits_is_blocking(self):
        jd = "Coordinate project reporting activities with internal stakeholders and external delivery partners across the program."
        content = "Coordinate project reporting activities with internal stakeholders and external project partners across this program."

        issues = find_jd_similarity_issues(content, jd)

        self.assertEqual(issues[0]["severity"], "error")
        self.assertIn("similarity", issues[0]["message"])

    def test_allowlisted_organisation_name_is_not_flagged(self):
        phrase = "Department of Transport and Major Infrastructure"

        self.assertEqual(find_jd_similarity_issues(phrase, phrase), [])

    def test_repeating_same_jd_fragment_twice_does_not_create_distinct_fragment_block(self):
        phrase = "project documentation contracts and technical records"
        issues = find_jd_similarity_issues(f"{phrase}. Later, {phrase}.", phrase)

        self.assertEqual(issues[0]["severity"], "warning")
        self.assertEqual(len(issues[0]["matches"]), 1)

    def test_normal_paraphrase_is_not_flagged(self):
        content = "My background includes maintaining records and balancing several active work streams."

        self.assertEqual(find_jd_similarity_issues(content, self.jd), [])


if __name__ == "__main__":
    unittest.main()
