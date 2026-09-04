import json
import unittest

from app.ckb import build_career_knowledge_base
from app.resume_plan import RESUME_PLAN_SCHEMA_VERSION, build_resume_curation_plan, evaluate_resume_quality, resume_evidence_pack, selected_resume_evidence_ids, validate_resume_content


def evidence(evidence_id, section, action="Grounded work", *, period=None, result=""):
    return {"evidence_id": evidence_id, "evidence_type": "experience", "source_section": section, "source_text": action,
            "time_period": period or {"start": None, "end": None}, "action": action, "evidence_quality": "medium", "result": result}


class ResumeCurationPlanTests(unittest.TestCase):
    def test_explicit_dates_control_presentation_while_relevance_controls_budget(self):
        ckb = [
            evidence("OLD", "Work > Older Relevant", "Executive support", period={"start": "2018", "end": "2020"}),
            evidence("NEW", "Work > Newer Role", "Administration", period={"start": "2023", "end": "2025"}),
        ]
        plan = build_resume_curation_plan(
            {"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]},
            {"matches": [{"criteria_id": "C1", "matched_evidence": ["OLD"], "match_type": "direct", "coverage": "strong"}]},
            ckb,
        )
        self.assertEqual([role["source_section"] for role in plan["roles"]], ["Work > Newer Role", "Work > Older Relevant"])
        self.assertEqual(plan["roles"][1]["curation_action"], "promote")
        self.assertEqual(plan["roles"][0]["display_period"], "2023 - 2025")

    def test_unknown_dates_keep_source_fallback_and_reliable_period_is_validated(self):
        ckb = [evidence("A", "Work > First"), evidence("B", "Work > Second")]
        plan = build_resume_curation_plan({"criteria": []}, {"matches": []}, ckb)
        self.assertEqual([role["source_section"] for role in plan["roles"]], ["Work > First", "Work > Second"])
        dated = build_resume_curation_plan(
            {"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]},
            {"matches": [{"criteria_id": "C1", "matched_evidence": ["A"], "match_type": "direct", "coverage": "strong"}]},
            [evidence("A", "Work > Officer", period={"start": "2022", "end": "2024"})],
        )
        result = validate_resume_content("## Professional Summary\nX\n## Key Skills\nX\n## Work Experience\nOfficer", dated, ["A"])
        self.assertIn("missing_role_period", [item["code"] for item in result["issues"]])

    def test_employment_period_must_be_inside_its_role_block(self):
        plan = {"required_sections": [], "selected_evidence": [], "roles": [
            {"role_marker": "Current Officer", "include_role_header": True, "display_period": "Feb 2026 - Present"},
            {"role_marker": "Earlier Officer", "include_role_header": True, "display_period": "Nov 2017 - Jan 2019"},
        ]}
        correct = validate_resume_content("Current Officer\nFeb 2026 – Present\nEarlier Officer\nNov 2017 – Jan 2019", plan, [])
        misplaced = validate_resume_content("Current Officer\nEarlier Officer\nFeb 2026 – Present\nNov 2017 – Jan 2019", plan, [])
        reversed_roles = validate_resume_content("Earlier Officer\nNov 2017 – Jan 2019\nCurrent Officer\nFeb 2026 – Present", plan, [])

        self.assertTrue(correct["valid"])
        self.assertIn("missing_role_period", [item["code"] for item in misplaced["issues"]])
        self.assertIn("role_order_mismatch", [item["code"] for item in reversed_roles["issues"]])

    def test_bennco_five_role_plan_validates_identity_dates_and_chronology(self):
        roles = [
            ("Finance Administration Officer", "Department of Communities – Disability Services", "Feb 2026 – Present"),
            ("Executive Assistant to Board Member", "Avaintec", "Nov 2017 – Jan 2019"),
            ("Project Administration Officer", "CCCC Kenya", "Jan 2016 – Aug 2017"),
            ("Project Administration Officer", "Chevron CDB Project", "Aug 2012 – Dec 2015"),
            ("Project Assistant", "Pratt & Whitney", "Oct 2007 – Aug 2012"),
        ]
        ckb = [
            evidence(
                f"BENNCO{index}", f"Work Experience > {organisation} > {role}",
                f"Provided distinct grounded support for employment role {index}.",
                period={"start": period.split(" – ")[0], "end": period.split(" – ")[1]},
            )
            for index, (role, organisation, period) in enumerate(roles)
        ]
        matches = {"matches": [{"criteria_id": "C1", "matched_evidence": [item["evidence_id"] for item in ckb], "match_type": "direct", "coverage": "strong"}]}
        plan = build_resume_curation_plan({"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]}, matches, ckb)

        self.assertEqual([item["time_period"]["end"] for item in ckb], ["Present", "Jan 2019", "Aug 2017", "Dec 2015", "Aug 2012"])
        self.assertEqual([role["display_period"] for role in plan["roles"]], [period.replace("–", "-") for _, _, period in roles])
        self.assertEqual([role["chronology_order"] for role in plan["roles"]], list(range(5)))
        self.assertEqual(
            [(role["employer_marker"], role["role_marker"]) for role in plan["roles"]],
            [(organisation, role) for role, organisation, _ in roles],
        )
        generated = """## Professional Summary
Grounded support.
## Key Skills
Administration
## Work Experience
### Department of Communities – Disability Services | Finance Administration Officer
Feb 2026 – Present
**Executive Assistant to Board Member**
**Avaintec**
Nov 2017 – Jan 2019
- Grounded executive support.
CCCC Kenya — Project Administration Officer
Jan 2016 - Aug 2017
Project Administration Officer | Chevron CDB Project
Aug 2012 — Dec 2015
Pratt & Whitney
Project Assistant
Oct 2007 - Aug 2012
"""
        self.assertTrue(validate_resume_content(generated, plan, [item["evidence_id"] for item in ckb])["valid"])

    def test_structured_role_headers_accept_layout_variants_without_fuzzy_titles(self):
        role = {
            "employer_marker": "Avaintec", "role_marker": "Executive Assistant to Board Member",
            "include_role_header": True, "display_period": "Nov 2017 - Jan 2019",
        }
        plan = {"required_sections": [], "selected_evidence": [], "roles": [role]}
        valid_headers = [
            "Avaintec — Executive Assistant to Board Member",
            "Executive Assistant to Board Member | Avaintec",
            "Avaintec\nExecutive Assistant to Board Member",
            "**Executive Assistant to Board Member**\n**Avaintec**",
            "### Avaintec - Executive Assistant to Board Member",
        ]
        for header in valid_headers:
            with self.subTest(header=header):
                self.assertTrue(validate_resume_content(f"{header}\nNov 2017 – Jan 2019", plan, [])["valid"])
        changed = validate_resume_content("Avaintec\nExecutive Assistant – Board Member\nNov 2017 – Jan 2019", plan, [])
        self.assertIn("missing_role_header", [item["code"] for item in changed["issues"]])

    def test_duplicate_titles_are_bound_to_employer_date_and_chronology(self):
        plan = {"required_sections": [], "selected_evidence": [], "roles": [
            {"employer_marker": "CCCC Kenya", "role_marker": "Project Administration Officer", "include_role_header": True, "display_period": "Jan 2016 - Aug 2017"},
            {"employer_marker": "Chevron CDB Project", "role_marker": "Project Administration Officer", "include_role_header": True, "display_period": "Aug 2012 - Dec 2015"},
        ]}
        correct = "CCCC Kenya\nProject Administration Officer\nJan 2016 – Aug 2017\nChevron CDB Project\nProject Administration Officer\nAug 2012 – Dec 2015"
        wrong_date = "CCCC Kenya\nProject Administration Officer\nAug 2012 – Dec 2015\nChevron CDB Project\nProject Administration Officer\nJan 2016 – Aug 2017"
        reversed_roles = "Chevron CDB Project\nProject Administration Officer\nAug 2012 – Dec 2015\nCCCC Kenya\nProject Administration Officer\nJan 2016 – Aug 2017"
        missing_employer = "Project Administration Officer\nJan 2016 – Aug 2017\nChevron CDB Project\nProject Administration Officer\nAug 2012 – Dec 2015"

        self.assertTrue(validate_resume_content(correct, plan, [])["valid"])
        self.assertIn("missing_role_period", [item["code"] for item in validate_resume_content(wrong_date, plan, [])["issues"]])
        self.assertIn("role_order_mismatch", [item["code"] for item in validate_resume_content(reversed_roles, plan, [])["issues"]])
        self.assertIn("missing_role_header", [item["code"] for item in validate_resume_content(missing_employer, plan, [])["issues"]])

    def test_omitted_or_ambiguous_structured_role_is_rejected(self):
        role = {"employer_marker": "Avaintec", "role_marker": "Executive Assistant to Board Member", "include_role_header": True, "display_period": ""}
        plan = {"required_sections": [], "selected_evidence": [], "roles": [role]}
        omitted = validate_resume_content("Other Employer\nExecutive Assistant", plan, [])
        repeated = validate_resume_content(
            "Avaintec | Executive Assistant to Board Member\nOther text\nAvaintec\nExecutive Assistant to Board Member", plan, [],
        )
        self.assertIn("missing_role_header", [item["code"] for item in omitted["issues"]])
        self.assertIn("ambiguous_role_header", [item["code"] for item in repeated["issues"]])

    def test_date_uncertainty_is_explicit_and_no_date_is_fabricated(self):
        uncertain = build_career_knowledge_base("", json.dumps([{
            "role_title": "Officer", "organization": "Example", "responsibility": "Administration",
            "source_text": "Officer at Example, approximately 2018",
        }]))[0]
        undated = build_career_knowledge_base("", json.dumps([{
            "role_title": "Assistant", "organization": "Example", "responsibility": "Administration",
            "source_text": "Assistant at Example",
        }]))[0]
        matches = {"matches": [{"criteria_id": "C1", "matched_evidence": [uncertain["evidence_id"], undated["evidence_id"]], "match_type": "direct", "coverage": "strong"}]}
        plan = build_resume_curation_plan({"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]}, matches, [uncertain, undated])

        self.assertEqual((uncertain["time_period_status"], uncertain["time_period"]), ("uncertain", {"start": None, "end": None}))
        self.assertEqual((undated["time_period_status"], undated["time_period"]), ("not_provided", {"start": None, "end": None}))
        self.assertEqual({role["date_status"] for role in plan["roles"]}, {"uncertain", "not_provided"})

    def test_relevance_controls_budget_while_source_order_controls_chronology(self):
        ckb = [
            evidence("FIN", "Work > Current Finance", "Processed accounts", period={"start": "2024", "end": "Present"}),
            evidence("EA1", "Work > Executive Assistant", "Managed executive diary", result="Reduced clashes"),
            evidence("EA2", "Work > Executive Assistant", "Coordinated board papers"),
            evidence("EA3", "Work > Executive Assistant", "Liaised with senior stakeholders"),
        ]
        model = {"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]}
        matches = {"matches": [{"criteria_id": "C1", "matched_evidence": ["EA1", "EA2", "EA3"], "match_type": "direct", "coverage": "strong"}]}
        plan = build_resume_curation_plan(model, matches, ckb)
        self.assertEqual(RESUME_PLAN_SCHEMA_VERSION, "1.1")
        self.assertEqual([role["source_section"] for role in plan["roles"]], ["Work > Current Finance", "Work > Executive Assistant"])
        self.assertEqual((plan["roles"][0]["curation_action"], plan["roles"][0]["max_bullets"]), ("compress", 1))
        self.assertEqual(plan["roles"][0]["selected_evidence_ids"], ["FIN"])
        self.assertEqual((plan["roles"][1]["curation_action"], plan["roles"][1]["max_bullets"]), ("promote", 3))
        self.assertEqual([item["evidence_id"] for item in plan["selected_evidence"]], ["FIN", "EA1", "EA2", "EA3"])
        self.assertEqual([item["evidence_id"] for item in resume_evidence_pack(json.dumps(ckb), json.dumps(plan))], ["FIN", "EA1", "EA2", "EA3"])

    def test_finance_target_promotes_current_finance_role(self):
        ckb = [evidence("FIN", "Work > Finance", "Prepared reconciliations", period={"start": "2024", "end": "Current"}), evidence("EA", "Work > EA")]
        plan = build_resume_curation_plan({"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]},
            {"matches": [{"criteria_id": "C1", "matched_evidence": ["FIN"], "match_type": "direct", "coverage": "strong"}]}, ckb)
        self.assertEqual(plan["roles"][0]["curation_action"], "promote")
        self.assertEqual(plan["roles"][0]["evidence_framing"], "direct")

    def test_inferred_match_strength_promotes_only_top_two_roles(self):
        ckb = [
            evidence(f"{role}{index}", f"Work > Role {role}", f"Distinct duty {role}-{index}")
            for role in "ABC" for index in range(3)
        ]
        criteria = [{"criteria_id": f"C{index}", "criteria_type": "inferred"} for index in range(3)]
        matches = {"matches": [{
            "criteria_id": f"C{index}", "matched_evidence": [f"{role}{index}" for role in "ABC"],
            "match_type": "direct", "coverage": "strong",
        } for index in range(3)]}

        plan = build_resume_curation_plan({"criteria": criteria}, matches, ckb)
        promoted = [role for role in plan["roles"] if role["curation_action"] == "promote"]

        self.assertEqual(len(promoted), 2)
        self.assertTrue(all(role["max_bullets"] == 3 for role in promoted))

    def test_objective_quality_gate_flags_the_audited_failure_shape(self):
        plan = {"target_words": 650, "roles": [
            {"include_role_header": True, "max_bullets": 1} for _ in range(5)
        ]}
        content = "## Work Experience\n" + "\n".join(
            f"### Role {index}\n- Provided administrative and project support." for index in range(5)
        )

        quality = evaluate_resume_quality(content, plan)

        self.assertEqual(quality["status"], "fail")
        self.assertEqual({item["type"] for item in quality["issues"]}, {
            "resume_too_brief", "resume_shallow_role_coverage", "resume_repetitive_opening",
        })

    def test_mixed_support_promotes_finance_without_upgrading_adjacent_evidence(self):
        section = "Work Experience > Department of Communities > Finance Administration Officer"
        ckb = [evidence(f"FIN{i}", section, f"Distinct source duty {i}") for i in range(3)]
        for kinds in (("direct", "direct", "inferred"), ("inferred",) * 3):
            matches = {"matches": [
                {"criteria_id": f"C{i}", "matched_evidence": [f"FIN{i}"], "match_type": kind}
                for i, kind in enumerate(kinds)
            ]}
            plan = build_resume_curation_plan({"criteria": []}, matches, ckb)
            self.assertEqual(plan["roles"][0]["curation_action"], "promote")
            self.assertEqual(plan["roles"][0]["max_bullets"], 3)
            adjacent = next(item for item in plan["selected_evidence"] if item["evidence_id"] == "FIN2")
            self.assertEqual((adjacent["curation_action"], adjacent["evidence_framing"]), ("feature", "adjacent"))
        for match in matches["matches"]:
            match["criteria_id"] = "SAME"
        plan = build_resume_curation_plan({"criteria": []}, matches, ckb)
        self.assertEqual(plan["roles"][0]["curation_action"], "keep")

    def test_word_quality_gate_at_trace_size_and_threshold(self):
        for words, expected in ((228, "fail"), (454, "fail"), (455, "pass")):
            quality = evaluate_resume_quality(" ".join(["word"] * words), {"target_words": 650})
            self.assertEqual(quality["word_count"], words)
            self.assertEqual(quality["status"], expected)

    def test_current_role_with_no_bullet_content_stays_visible_without_filler(self):
        current = evidence("NOW", "Work > Current Role", "", period={"start": "2025", "end": "Present"})
        plan = build_resume_curation_plan({"criteria": []}, {"matches": []}, [current])
        self.assertEqual(plan["roles"][0]["curation_action"], "compress")
        self.assertEqual(plan["roles"][0]["max_bullets"], 0)
        self.assertTrue(plan["roles"][0]["include_role_header"])
        self.assertEqual(plan["roles"][0]["selected_evidence_ids"], [])
        self.assertNotIn("NOW", selected_resume_evidence_ids(plan))

    def test_omit_role_header_semantics_are_explicit(self):
        plan = build_resume_curation_plan({"criteria": []}, {"matches": []}, [evidence("OLD", "Work > Old Role")])
        self.assertEqual(plan["roles"][0]["curation_action"], "omit")
        self.assertFalse(plan["roles"][0]["include_role_header"])

    def test_direct_outranks_adjacent_and_adjacent_stays_explicit(self):
        model = {"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}, {"criteria_id": "C2", "criteria_type": "essential"}]}
        matches = {"matches": [{"criteria_id": "C1", "matched_evidence": ["ADJ"], "match_type": "inferred", "coverage": "strong"},
                               {"criteria_id": "C2", "matched_evidence": ["DIR"], "match_type": "direct", "coverage": "partial"}]}
        ckb = [evidence("ADJ", "Work > Adjacent", "Transferable coordination"), evidence("DIR", "Work > Direct", "Direct delivery")]
        plan = build_resume_curation_plan(model, matches, ckb)
        by_id = {item["evidence_id"]: item for item in plan["selected_evidence"]}
        self.assertEqual((by_id["ADJ"]["evidence_framing"], by_id["DIR"]["evidence_framing"]), ("adjacent", "direct"))
        self.assertEqual(next(role for role in plan["roles"] if role["source_section"] == "Work > Direct")["curation_action"], "promote")
        constrained = build_resume_curation_plan(model, matches, ckb, max_evidence=1)
        self.assertEqual([item["evidence_id"] for item in constrained["selected_evidence"]], ["DIR"])

    def test_decision_gaps_and_unsupported_requirements_add_no_evidence(self):
        model = {"criteria": [{"criteria_id": value, "criteria_type": kind} for value, kind in (("G", "essential"), ("U", "essential"), ("D", "desirable"))]}
        matches = {"matches": [{"criteria_id": value, "matched_evidence": [], "match_type": "insufficient", "coverage": "weak"} for value in ("G", "U", "D")]}
        decision = {"application_recommendation": "apply", "requirements": [
            {"criteria_id": "G", "importance": "essential", "evidence_classification": "confirmed_gap", "matched_evidence": []},
            {"criteria_id": "U", "importance": "essential", "evidence_classification": "unverified_possible", "matched_evidence": []},
            {"criteria_id": "D", "importance": "desirable", "evidence_classification": None, "matched_evidence": []}]}
        plan = build_resume_curation_plan(model, matches, [evidence("OTHER", "Work > Other")], application_decision=decision)
        self.assertEqual(plan["selected_evidence"], [])

    def test_duplicate_grounded_content_is_selected_once(self):
        matches = {"matches": [{"criteria_id": "C1", "matched_evidence": ["ONE", "TWO"], "match_type": "direct", "coverage": "strong"}]}
        plan = build_resume_curation_plan({"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]}, matches,
            [evidence("ONE", "Work > First", "Prepared monthly reports"), evidence("TWO", "Work > Other", "Prepared monthly reports")])
        self.assertEqual(len(plan["selected_evidence"]), 1)

    def test_page_pressure_caps_evidence_without_reordering_roles(self):
        ckb = [evidence(f"EV{i}", f"Work > Role {i}", f"Distinct work {i}") for i in range(12)]
        matches = {"matches": [{"criteria_id": "C1", "matched_evidence": [item["evidence_id"] for item in reversed(ckb)], "match_type": "direct", "coverage": "strong"}]}
        plan = build_resume_curation_plan({"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]}, matches, ckb, max_evidence=6)
        self.assertEqual(len(plan["selected_evidence"]), 6)
        self.assertEqual([role["chronology_order"] for role in plan["roles"]], list(range(12)))

    def test_qualification_is_retained_and_limit_is_respected(self):
        ckb = [{"evidence_id": f"EV{i}", "evidence_type": "qualification", "source_section": "Qualifications", "source_text": f"Qualification {i}", "evidence_quality": "medium"} for i in range(20)]
        self.assertEqual(len(build_resume_curation_plan({"criteria": []}, {"matches": []}, ckb, max_evidence=6)["selected_evidence"]), 6)

    def test_fallback_qualifications_do_not_displace_current_continuity(self):
        ckb = [evidence("NOW", "Work > Current", "Current grounded duty", period={"start": "2025", "end": "Present"})]
        ckb += [{"evidence_id": f"Q{i}", "evidence_type": "qualification", "source_section": "Qualifications", "source_text": f"Qualification {i}", "evidence_quality": "medium"} for i in range(4)]
        plan = build_resume_curation_plan({"criteria": []}, {"matches": []}, ckb, max_evidence=2)
        self.assertIn("NOW", selected_resume_evidence_ids(plan))
        self.assertEqual(len(plan["selected_evidence"]), 2)

    def test_relevant_qualification_survives(self):
        ckb = [{"evidence_id": "RQ", "evidence_type": "qualification", "source_section": "Qualifications", "source_text": "Required certificate", "detail": "Required certificate", "evidence_quality": "medium"}]
        plan = build_resume_curation_plan(
            {"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]},
            {"matches": [{"criteria_id": "C1", "matched_evidence": ["RQ"], "match_type": "direct", "coverage": "strong"}]}, ckb, max_evidence=1,
        )
        self.assertEqual([item["evidence_id"] for item in plan["selected_evidence"]], ["RQ"])

    def test_distinct_essential_requirements_receive_coverage_before_depth(self):
        ckb = [evidence("A1", "Work > A", "A strongest", result="Result"), evidence("A2", "Work > A", "A depth"), evidence("B1", "Work > B", "B coverage")]
        plan = build_resume_curation_plan(
            {"criteria": [{"criteria_id": "A", "criteria_type": "essential"}, {"criteria_id": "B", "criteria_type": "essential"}]},
            {"matches": [{"criteria_id": "A", "matched_evidence": ["A1", "A2"], "match_type": "direct", "coverage": "strong"}, {"criteria_id": "B", "matched_evidence": ["B1"], "match_type": "direct", "coverage": "strong"}]},
            ckb, max_evidence=2,
        )
        self.assertEqual(selected_resume_evidence_ids(plan), {"A1", "B1"})

    def test_hard_validation_requires_sections_length_and_selected_evidence(self):
        plan = {"required_sections": ["Professional Summary", "Key Skills", "Work Experience"], "selected_evidence": [{"evidence_id": "EV1"}]}
        valid = "## Professional Summary\nSummary.\n## Key Skills\nSkills.\n## Work Experience\nExperience."
        self.assertTrue(validate_resume_content(valid, plan, ["EV1"])["valid"])
        invalid = validate_resume_content("## Professional Summary\n" + " ".join(["word"] * 751), plan, ["EV2"])
        self.assertEqual({issue["code"] for issue in invalid["issues"]}, {"missing_required_section", "resume_too_long", "unselected_evidence_used"})


if __name__ == "__main__":
    unittest.main()
