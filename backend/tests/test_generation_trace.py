import unittest
from datetime import datetime
from types import SimpleNamespace

from app.generation_trace import GENERATION_TRACE_SCHEMA_VERSION, build_generation_trace, build_trace_bundle


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
            latency_ms=1250,
            retry_count=2,
            ai_runtime={
                "call_count": 2, "call_limits": {"generation": 1, "review": 1},
                "input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500,
                "estimated_cost": 0.0123, "calls": [],
            },
        )

        self.assertEqual(GENERATION_TRACE_SCHEMA_VERSION, "1.1")
        self.assertEqual(trace["run_id"], "run-123")
        self.assertEqual(trace["input_refs"], {
            "application_id": 7, "resume_id": 3, "profile_id": None, "context_fingerprint": "",
        })
        self.assertEqual(trace["trace"]["evidence_ids"], ["EV001", "EV002"])
        self.assertEqual(trace["review"], {"status": "fail", "finding_count": 1})
        self.assertEqual(trace["versions"]["government_writing_rules"], "1.1")
        self.assertEqual(trace["versions"]["applicant_profile_schema"], "1.0")
        self.assertEqual(trace["runtime"]["status"], "completed")
        self.assertEqual(trace["runtime"]["latency_ms"], 1250)
        self.assertEqual(trace["runtime"]["observed_retry_count"], 2)
        self.assertEqual(trace["runtime"]["ai_calls"]["total_tokens"], 1500)
        self.assertEqual(trace["runtime"]["ai_calls"]["estimated_cost"], 0.0123)

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

    def test_export_bundle_contains_plan_review_evidence_and_final_output(self):
        document = SimpleNamespace(
            id=9, run_id="run-789", application_id=7, document_type="cover_letter",
            created_at=datetime(2026, 8, 9, 10, 0, 0),
            trace_json='{"schema_version":"1.0"}',
            structured_content_json='{"priorities":[{"criteria_id":"C1"}]}',
            reviewer_json='{"status":"pass","results":[]}',
            used_experiences_json='["EV001"]',
            content="Final cover letter text.",
        )

        bundle = build_trace_bundle(document)

        self.assertEqual(bundle["bundle_schema_version"], "1.0")
        self.assertEqual(bundle["manifest"]["schema_version"], "1.0")
        self.assertEqual(bundle["generation_plan"]["priorities"][0]["criteria_id"], "C1")
        self.assertEqual(bundle["used_evidence_ids"], ["EV001"])
        self.assertEqual(bundle["final_output"], document.content)


if __name__ == "__main__":
    unittest.main()
