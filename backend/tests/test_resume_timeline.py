import unittest
from datetime import date

from app.resume_timeline import apply_timeline, timeline_text


def plan(periods, today=date(2026, 9, 4)):
    roles, sources = [], {}
    for index, (start, end, framing) in enumerate(periods):
        section = str(index)
        roles.append({"source_section": section, "role_marker": f"Role {index}", "employer_marker": f"Employer {index}",
                      "display_period": f"{start} - {end}", "evidence_framing": framing, "include_role_header": framing == "direct"})
        sources[section] = [{"time_period": {"start": start, "end": end}}]
    timeline = apply_timeline(roles, sources, today)
    return roles, timeline


class ResumeTimelineTests(unittest.TestCase):
    def test_tests_1_2_and_exact_twelve_month_boundary(self):
        for next_start, expected in [("Dec 2022", "hidden"), ("Jan 2023", "hidden"), ("Jul 2023", "timeline_only")]:
            roles, _ = plan([("Jan 2021", "Jan 2022", "direct"), ("Feb 2022", "Nov 2022", None), (next_start, "Present", "direct")])
            self.assertEqual(roles[1]["display_mode"], expected)

    def test_test_3_relevance_changes_with_match(self):
        for framing, mode in [("direct", "full"), ("adjacent", "condensed")]:
            roles, _ = plan([("Jan 2020", "Present", framing)])
            self.assertEqual(roles[0]["display_mode"], mode)

    def test_tests_4_6_11_12_timeline_is_not_expandable_and_merge_is_bounded(self):
        for second_start, count in [("Jun 2023", 1), ("Jul 2023", 1), ("Sep 2023", 2)]:
            roles, timeline = plan([("Jan 2020", "Jan 2022", "direct"), ("Jul 2022", "May 2023", None),
                                    (second_start, "Mar 2024", None), ("Jul 2024", "Present", "direct")])
            self.assertEqual(len(timeline["groups"]), count)
            for role in roles[1:3]:
                self.assertEqual(role["display_mode"], "timeline_only")
                self.assertEqual(role["max_bullets"], 0)
                self.assertEqual(role["selected_evidence_ids"], [])

    def test_tests_7_9_real_gaps_have_no_placeholder(self):
        _, timeline = plan([("Feb 2026", "Aug 2026", "direct")], date(2027, 10, 1))
        self.assertEqual(timeline["groups"], [])

    def test_test_8_recent_end_and_current_employment(self):
        for end in ("Aug 2026", "Present", "ongoing"):
            _, timeline = plan([("Feb 2026", end, "direct")])
            self.assertEqual(timeline["groups"], [])

    def test_test_10_partial_gap_keeps_exact_supported_period(self):
        _, timeline = plan([("Jan 2020", "Jan 2022", "direct"), ("Jul 2022", "Mar 2023", None), ("Jan 2024", "Present", "direct")])
        text = timeline_text({"timeline": timeline})
        self.assertIn("Jul 2022 - Mar 2023", text)
        self.assertNotIn("Jan 2022", text)
        self.assertNotIn("Jan 2024", text)

    def test_long_tail_restores_only_existing_employment(self):
        roles, _ = plan([("Jan 2020", "Jan 2022", "direct"), ("Jul 2022", "Present", None)])
        self.assertEqual(roles[1]["display_mode"], "timeline_only")

    def test_unknown_dates_are_reported_without_inventing_periods(self):
        roles, timeline = plan([("unknown", "unknown", None)])
        self.assertEqual(roles[0]["display_mode"], "hidden")
        self.assertEqual(timeline["uncertain_sections"], ["0"])


if __name__ == "__main__":
    unittest.main()
