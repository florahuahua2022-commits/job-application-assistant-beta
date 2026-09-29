import json
import unittest

from app.evidence_matcher import matched_evidence_pack, normalise_match_result, validate_match_result


class EvidenceMatcherTests(unittest.TestCase):
    def setUp(self):
        self.ckb = [
            {"evidence_id": "EV001", "evidence_type": "experience", "source_section": "Work", "source_text": "Prepared monthly project reports."},
            {"evidence_id": "EV002", "evidence_type": "education", "source_section": "Education", "source_text": "Bachelor of Business."},
        ]
        self.job_model = {"criteria": [
            {"criteria_id": "C1", "criteria_text": "Reporting experience"},
            {"criteria_id": "C2", "criteria_text": "Relevant qualification"},
        ]}

    def test_filters_unknown_evidence_and_fills_missing_criterion(self):
        raw = {"matches": [{
            "criteria_id": "C1", "matched_evidence": ["EV001", "INVENTED"],
            "match_type": "direct", "coverage": "strong", "reasoning": "Direct reporting evidence.",
        }]}

        result = normalise_match_result(raw, self.job_model, self.ckb)

        self.assertEqual(result["matches"][0]["matched_evidence"], ["EV001"])
        self.assertEqual(result["matches"][1]["criteria_id"], "C2")
        self.assertEqual(result["matches"][1]["match_type"], "insufficient")
        self.assertEqual(result["unused_evidence"], ["EV002"])
        self.assertEqual(validate_match_result(result, self.job_model, self.ckb), [])

    def test_builds_generation_pack_only_from_matched_ids(self):
        matches = {"matches": [{"criteria_id": "C1", "matched_evidence": ["EV001"]}]}

        pack = matched_evidence_pack(json.dumps(self.ckb), json.dumps(matches))

        self.assertEqual([item["evidence_id"] for item in pack], ["EV001"])
        self.assertEqual(pack[0]["source_text"], "Prepared monthly project reports.")

    def test_chevron_stakeholder_engagement_supports_communication_but_not_diverse_backgrounds(self):
        criterion = {
            "criteria_id": "C2A07CC8192",
            "criteria_text": "Excellent communication and collaboration skills, with the ability to work effectively with stakeholders from diverse backgrounds.",
        }
        source = (
            "Supported project delivery through administration, coordination and stakeholder engagement across multidisciplinary teams. "
            "Collated information from government agencies, consultants and community representatives; supported large-scale community consultation activities."
        )
        result = normalise_match_result({"matches": [{
            "criteria_id": criterion["criteria_id"], "matched_evidence": ["EV1BFA570A50FD"],
            "match_type": "direct", "coverage": "strong", "reasoning": "Stakeholder work.",
            "evidence_support": [{"evidence_id": "EV1BFA570A50FD", "support_quote": "Supported project delivery through administration, coordination and stakeholder engagement across multidisciplinary teams.", "matched_requirement_terms": ["communication", "collaboration"]}],
        }]}, {"criteria": [criterion]}, [{"evidence_id": "EV1BFA570A50FD", "source_text": source}])

        match = result["matches"][0]
        support = match["evidence_support"][0]
        self.assertEqual((match["match_type"], match["coverage"]), ("inferred", "partial"))
        self.assertEqual(support["supported_atoms"], ["communication", "collaboration"])
        self.assertIn("diverse_backgrounds", support["unsupported_atoms"])

    def test_standalone_engagement_does_not_count_as_communication(self):
        criterion = {"criteria_id": "COMM", "criteria_text": "Excellent communication skills"}
        result = normalise_match_result({"matches": [{
            "criteria_id": "COMM", "matched_evidence": ["EV"], "match_type": "direct", "coverage": "strong",
            "evidence_support": [{"evidence_id": "EV", "support_quote": "Maintained engagement with internal systems.", "matched_requirement_terms": ["communication"]}],
        }]}, {"criteria": [criterion]}, [{"evidence_id": "EV", "source_text": "Maintained engagement with internal systems."}])

        self.assertEqual((result["matches"][0]["match_type"], result["matches"][0]["coverage"]), ("insufficient", "weak"))

    def test_core_color_scheduling_quote_can_remain_direct_for_organisation_and_time_management(self):
        criterion = {"criteria_id": "C493EC45DAE", "criteria_text": "Has excellent organisational and time-management skills"}
        quote = "Developing and executing brand, content and advertising through social media posts and scheduling post, coordination of content schedules for event plans, product launches, membership programmes."
        result = normalise_match_result({"matches": [{
            "criteria_id": criterion["criteria_id"], "matched_evidence": ["EV99B6E0838BD4"], "match_type": "direct", "coverage": "strong",
            "evidence_support": [{"evidence_id": "EV99B6E0838BD4", "support_quote": quote, "matched_requirement_terms": ["organisational", "time-management"]}],
        }]}, {"criteria": [criterion]}, [{"evidence_id": "EV99B6E0838BD4", "source_text": quote}])

        match = result["matches"][0]
        self.assertEqual((match["match_type"], match["coverage"]), ("direct", "strong"))
        self.assertEqual(match["evidence_support"][0]["supported_atoms"], ["organisation", "time_management"])

    def test_application_42_real_evidence_links_follow_confirmed_grounding_outcomes(self):
        cases = [
            ("C90DCEF1954", "Excellent time management and organisational skills.", "EV849245D0F4F5", "Delivered individualised support with daily living, community access and appointments, maintaining service documentation. Adapted support to different client needs and communicated with clients and relevant stakeholders.", "Adapted support to different client needs and communicated with clients and relevant stakeholders.", ("insufficient", "weak")),
            ("C90DCEF1954", "Excellent time management and organisational skills.", "EV2FAF7A3012FA", "Coordinated internal and external meetings, workshops and events, including logistics and stakeholder engagement. Planned a high-level international visit over three months for approximately 10 delegates, managing end-to-end logistics across China and Finland.", "Planned a high-level international visit over three months for approximately 10 delegates, managing end-to-end logistics across China and Finland.", ("inferred", "partial")),
            ("C6153F488B0", "Ability to multitask.", "EV2572ECCB036E", "Managed scheduling and direct client communication independently while maintaining professional service records.", "Managed scheduling and direct client communication independently while maintaining professional service records.", ("insufficient", "weak")),
            ("C6153F488B0", "Ability to multitask.", "EV849245D0F4F5", "Delivered individualised support with daily living, community access and appointments, maintaining service documentation.", "Delivered individualised support with daily living, community access and appointments, maintaining service documentation.", ("insufficient", "weak")),
            ("C6153F488B0", "Ability to multitask.", "EV2FAF7A3012FA", "Provided executive support to Board-level leadership across strategic and operational activities.", "Provided executive support to Board-level leadership across strategic and operational activities.", ("insufficient", "weak")),
            ("C2A07CC8192", "Excellent communication and collaboration skills, with the ability to work effectively with stakeholders from diverse backgrounds.", "EV51BD90C637AE", "Collated project information from government agencies, contractors and internal teams. Liaised with government authorities, contractors and service providers; communicated with external lawyers.", "Liaised with government authorities, contractors and service providers; communicated with external lawyers.", ("inferred", "partial")),
            ("C2A07CC8192", "Excellent communication and collaboration skills, with the ability to work effectively with stakeholders from diverse backgrounds.", "EVA37A578823CE", "Liaised with internal stakeholders to resolve queries and support operational workflows.", "Liaised with internal stakeholders to resolve queries and support operational workflows.", ("inferred", "partial")),
            ("C2A07CC8192", "Excellent communication and collaboration skills, with the ability to work effectively with stakeholders from diverse backgrounds.", "EV1BFA570A50FD", "Supported project delivery through administration, coordination and stakeholder engagement across multidisciplinary teams.", "Supported project delivery through administration, coordination and stakeholder engagement across multidisciplinary teams.", ("inferred", "partial")),
            ("C2A07CC8192", "Excellent communication and collaboration skills, with the ability to work effectively with stakeholders from diverse backgrounds.", "EV2572ECCB036E", "Managed scheduling and direct client communication independently while maintaining professional service records.", "Managed scheduling and direct client communication independently while maintaining professional service records.", ("inferred", "partial")),
        ]
        for criterion_id, criterion_text, evidence_id, source, quote, expected in cases:
            raw = {"matches": [{
                "criteria_id": criterion_id, "matched_evidence": [evidence_id], "match_type": "direct", "coverage": "strong",
                "evidence_support": [{"evidence_id": evidence_id, "support_quote": quote, "matched_requirement_terms": []}],
            }]}
            with self.subTest(evidence_id=evidence_id, criterion_id=criterion_id):
                result = normalise_match_result(raw, {"criteria": [{"criteria_id": criterion_id, "criteria_text": criterion_text}]}, [{"evidence_id": evidence_id, "source_text": source}])
                self.assertEqual((result["matches"][0]["match_type"], result["matches"][0]["coverage"]), expected)


if __name__ == "__main__":
    unittest.main()
