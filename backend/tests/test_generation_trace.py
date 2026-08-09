import unittest

from app.generation_trace import GENERATION_TRACE_SCHEMA_VERSION, build_generation_trace


class GenerationTraceTests(unittest.TestCase):
    def test_trace_records_versions_inputs_model_evidence_and_review(self):
        trace = build_generation_trace(
            run_id="run-123",
            document_type="selection_criteria",
            application_id=7,
            resume_id=3,
            provider="openai",
            model="gpt-5",
            evidence_ids=["EV002", "EV001", "EV002"],
            reviewer={"status": "fail", "results": [
                {"criteria_id": "C1", "issues": [{"type": "fabricated_figure"}]},
                {"criteria_id": "C2", "issues": []},
            ]},
        )

        self.assertEqual(GENERATION_TRACE_SCHEMA_VERSION, "1.0")
        self.assertEqual(trace["run_id"], "run-123")
        self.assertEqual(trace["input_refs"], {"application_id": 7, "resume_id": 3})
        self.assertEqual(trace["trace"]["evidence_ids"], ["EV001", "EV002"])
        self.assertEqual(trace["review"], {"status": "fail", "finding_count": 1})
        self.assertEqual(trace["versions"]["government_writing_rules"], "1.0")
        self.assertEqual(trace["versions"]["applicant_profile_schema"], "1.0")

    def test_non_reviewed_document_is_explicit(self):
        trace = build_generation_trace(
            run_id="run-456",
            document_type="cover_letter",
            application_id=7,
            resume_id=3,
            provider="deepseek",
            model="deepseek-v4-flash",
            evidence_ids=[],
        )

        self.assertEqual(trace["review"], {"status": "not_run", "finding_count": 0})
        self.assertTrue(trace["created_at"].endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
