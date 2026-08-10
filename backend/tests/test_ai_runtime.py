import unittest

from app.ai_runtime import (
    AICallBudgetExceeded,
    ai_call_scope,
    begin_ai_run,
    current_ai_run,
    end_ai_run,
    record_ai_call,
    start_ai_call,
)


class AIRuntimeTests(unittest.TestCase):
    def test_records_calls_tokens_cost_latency_and_retry_reason(self):
        token = begin_ai_run("cover_letter")
        try:
            with ai_call_scope("generation", "position_title_missing"):
                call = start_ai_call()
                record_ai_call(
                    call,
                    provider="openai",
                    model="test-model",
                    input_tokens=120,
                    output_tokens=30,
                    estimated_cost=0.0042,
                )
            snapshot = current_ai_run().snapshot()
        finally:
            end_ai_run(token)

        self.assertEqual(snapshot["call_count"], 1)
        self.assertEqual(snapshot["total_tokens"], 150)
        self.assertEqual(snapshot["estimated_cost"], 0.0042)
        self.assertEqual(snapshot["calls"][0]["retry_reason"], "position_title_missing")
        self.assertGreaterEqual(snapshot["calls"][0]["latency_ms"], 0)

    def test_cover_letter_stops_before_third_generation_call(self):
        token = begin_ai_run("cover_letter")
        try:
            for _ in range(2):
                call = start_ai_call()
                record_ai_call(
                    call, provider="openai", model="test", input_tokens=1,
                    output_tokens=1, estimated_cost=0,
                )
            with self.assertRaises(AICallBudgetExceeded):
                start_ai_call()
        finally:
            end_ai_run(token)

    def test_reviewer_is_limited_to_one_call(self):
        token = begin_ai_run("tailored_resume")
        try:
            with ai_call_scope("review"):
                call = start_ai_call()
                record_ai_call(
                    call, provider="deepseek", model="test", input_tokens=1,
                    output_tokens=1, estimated_cost=0,
                )
            with ai_call_scope("review", "invalid_reviewer_result"):
                with self.assertRaises(AICallBudgetExceeded):
                    start_ai_call()
        finally:
            end_ai_run(token)

    def test_selection_reviewer_allows_one_invalid_result_retry(self):
        token = begin_ai_run("selection_criteria", criterion_count=5)
        try:
            for reason in ("", "invalid_reviewer_result"):
                with ai_call_scope("review", reason):
                    call = start_ai_call()
                    record_ai_call(
                        call, provider="deepseek", model="test", input_tokens=1,
                        output_tokens=1, estimated_cost=0,
                    )
            with ai_call_scope("review", "invalid_reviewer_result"):
                with self.assertRaises(AICallBudgetExceeded):
                    start_ai_call()
        finally:
            end_ai_run(token)


if __name__ == "__main__":
    unittest.main()
