import io
import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app import ai
from app import main


class EvidenceMatchDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        ai.evidence_diagnostics.addHandler(self.handler)
        ai.evidence_diagnostics.setLevel(logging.INFO)
        self.addCleanup(ai.evidence_diagnostics.removeHandler, self.handler)

    def events(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines() if line.strip()]

    def test_safe_log_emits_only_whitelisted_fields(self):
        ai.safe_evidence_log({
            "event": "evidence_match_provider_attempt", "run_id": "run-123",
            "criterion_id": "C1", "prompt_characters": 123,
            "api_key": "SECRET_KEY_FIXTURE", "prompt": "PRIVATE_MASTER_FIXTURE",
            "authorization": "Bearer PRIVATE_TOKEN_FIXTURE", "response_body": "PRIVATE_RESPONSE_FIXTURE",
            "support_quote": "PRIVATE_SUPPORT_FIXTURE", "name": "PRIVATE_NAME_FIXTURE",
            "email": "private@example.invalid", "phone": "0400000000", "address": "PRIVATE_ADDRESS_FIXTURE",
        })

        rendered = self.stream.getvalue()
        self.assertIn('"run_id":"run-123"', rendered)
        self.assertIn('"finish_reason":null', rendered)
        for secret in (
            "SECRET_KEY_FIXTURE", "PRIVATE_MASTER_FIXTURE", "PRIVATE_TOKEN_FIXTURE", "PRIVATE_RESPONSE_FIXTURE",
            "PRIVATE_SUPPORT_FIXTURE", "PRIVATE_NAME_FIXTURE", "private@example.invalid", "0400000000",
            "PRIVATE_ADDRESS_FIXTURE",
        ):
            self.assertNotIn(secret, rendered)

        ai.safe_evidence_log({"event": "application_diagnosis_complete", "model": "x" * 121})
        self.assertEqual(self.events()[-1]["model"], "x" * 120)

    def test_log_failure_never_breaks_diagnosis(self):
        with patch.object(ai.evidence_diagnostics, "info", side_effect=RuntimeError("synthetic logger failure")):
            ai.safe_evidence_log({"event": "application_diagnosis_complete", "run_id": "run-safe"})

    def test_transport_records_success_and_no_response_attempt_without_payloads(self):
        requests = []

        def send(request):
            requests.append(request)
            if len(requests) == 1:
                raise httpx.ConnectError("PRIVATE_NETWORK_FIXTURE", request=request)
            return httpx.Response(200, headers={"x-request-id": "provider-request-2"}, request=request)

        transport = ai.EvidenceAttemptTransport(httpx.MockTransport(send))
        token = ai.begin_evidence_provider_attempts({
            "run_id": "run-transport", "application_id": 7, "criterion_id": "C2",
            "criterion_index": 2, "application_attempt": 1, "provider": "deepseek",
            "model": "synthetic-model", "prompt_characters": 321, "evidence_count": 4,
        })
        try:
            with httpx.Client(transport=transport) as client:
                with self.assertRaises(httpx.ConnectError):
                    client.get("https://synthetic.invalid", headers={"x-stainless-retry-count": "0"})
                response = client.get("https://synthetic.invalid", headers={"x-stainless-retry-count": "1"})
                self.assertEqual(response.status_code, 200)
        finally:
            ai.end_evidence_provider_attempts(token)

        events = self.events()
        self.assertEqual([event["sdk_retry_count"] for event in events], [0, 1])
        self.assertEqual(events[0]["exception_type"], "ConnectError")
        self.assertIsNone(events[0]["http_status"])
        self.assertEqual(events[1]["http_status"], 200)
        self.assertEqual(events[1]["provider_request_id"], "provider-request-2")
        self.assertTrue(all(event["elapsed_ms"] >= 0 for event in events))
        self.assertNotIn("PRIVATE_NETWORK_FIXTURE", self.stream.getvalue())
        self.assertNotIn("synthetic.invalid", self.stream.getvalue())

    def test_openai_sdk_retry_attempts_are_observed_without_changing_retry_count(self):
        calls = []

        def send(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(500, json={"error": {"message": "busy"}}, request=request)
            return httpx.Response(200, headers={"x-request-id": "sdk-request-2"}, json={
                "id": "synthetic-response", "model": "synthetic-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            }, request=request)

        client = ai.instrument_default_http_client(ai.DefaultHttpxClient(transport=httpx.MockTransport(send)))
        token = ai.begin_evidence_provider_attempts({"run_id": "run-sdk", "provider": "deepseek", "model": "synthetic-model"})
        try:
            provider = ai.OpenAI(api_key="synthetic-key", base_url="https://synthetic.invalid", max_retries=1, http_client=client)
            response = provider.chat.completions.create(model="synthetic-model", messages=[{"role": "user", "content": "synthetic"}])
            self.assertEqual(response.choices[0].message.content, "{}")
        finally:
            ai.end_evidence_provider_attempts(token)
            client.close()

        self.assertEqual(len(calls), 2)
        self.assertEqual([event["sdk_retry_count"] for event in self.events()], [0, 1])

    def test_instrumentation_failure_restores_plain_client_and_preserves_result(self):
        requests = []

        def send(request):
            requests.append(request)
            return httpx.Response(200, request=request, json={
                "id": "synthetic-response", "model": "synthetic-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            })

        client = ai.DefaultHttpxClient(transport=httpx.MockTransport(send))
        with patch.object(ai, "EvidenceAttemptTransport", side_effect=RuntimeError("synthetic hook failure")):
            returned = ai.instrument_default_http_client(client)
        provider = ai.OpenAI(
            api_key="synthetic-key", base_url="https://synthetic.invalid", max_retries=0, http_client=returned,
        )
        response = provider.chat.completions.create(
            model="synthetic-model", messages=[{"role": "user", "content": "synthetic"}],
        )

        self.assertEqual(response.choices[0].message.content, "{}")
        self.assertEqual(len(requests), 1)
        failure = [event for event in self.events() if event["event"] == "evidence_match_instrumentation_failed"]
        self.assertEqual(failure[-1]["exception_type"], "RuntimeError")
        client.close()

    def test_instrumentation_missing_transport_and_mounts_returns_original_object(self):
        client = SimpleNamespace()

        self.assertIs(ai.instrument_default_http_client(client), client)

        failure = [event for event in self.events() if event["event"] == "evidence_match_instrumentation_failed"]
        self.assertEqual(failure[-1]["exception_type"], "AttributeError")

    def test_instrumentation_missing_mounts_restores_existing_transport(self):
        transport = object()
        client = SimpleNamespace(_transport=transport)

        self.assertIs(ai.instrument_default_http_client(client), client)

        self.assertIs(client._transport, transport)
        failure = [event for event in self.events() if event["event"] == "evidence_match_instrumentation_failed"]
        self.assertEqual(failure[-1]["exception_type"], "AttributeError")

    def test_deepseek_hook_preserves_configured_request_timeout(self):
        requests = []

        def send(request):
            requests.append(request)
            return httpx.Response(200, request=request, json={
                "id": "synthetic-response", "model": "synthetic-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            })

        client = ai.DefaultHttpxClient(transport=httpx.MockTransport(send))
        token = ai.begin_evidence_provider_attempts({"run_id": "run-timeout"})
        try:
            with patch.object(ai, "DefaultHttpxClient", return_value=client), patch.object(
                ai.settings, "deepseek_api_key", "synthetic-key",
            ), patch.object(ai.settings, "deepseek_base_url", "https://synthetic.invalid"), patch.object(
                ai.settings, "ai_request_timeout_seconds", 37.0,
            ):
                self.assertEqual(ai._deepseek_draft("synthetic"), "{}")
        finally:
            ai.end_evidence_provider_attempts(token)
            client.close()

        self.assertEqual(requests[0].extensions["timeout"], {
            "connect": 37.0, "read": 37.0, "write": 37.0, "pool": 37.0,
        })

    def test_openai_hook_preserves_default_request_timeout(self):
        hooked_requests = []
        plain_requests = []

        def response(request, target):
            target.append(request)
            return httpx.Response(200, request=request, json={
                "id": "synthetic-response", "model": "synthetic-model", "output": [],
                "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            })

        plain_client = ai.DefaultHttpxClient(
            transport=httpx.MockTransport(lambda request: response(request, plain_requests)),
        )
        hooked_client = ai.DefaultHttpxClient(
            transport=httpx.MockTransport(lambda request: response(request, hooked_requests)),
        )
        plain_provider = ai.OpenAI(api_key="synthetic-key", http_client=plain_client)
        plain_provider.responses.create(model="synthetic-model", input="synthetic")
        token = ai.begin_evidence_provider_attempts({"run_id": "run-openai-timeout"})
        try:
            hooked_provider = ai.OpenAI(
                api_key="synthetic-key", http_client=ai.instrument_default_http_client(hooked_client),
            )
            hooked_provider.responses.create(model="synthetic-model", input="synthetic")
        finally:
            ai.end_evidence_provider_attempts(token)
            plain_client.close()
            hooked_client.close()

        self.assertEqual(hooked_requests[0].extensions["timeout"], plain_requests[0].extensions["timeout"])

    def test_provider_failures_are_classified_without_exposing_statuses(self):
        class ProviderError(Exception):
            def __init__(self, status_code=None):
                self.status_code = status_code

        for error, category in (
            (ProviderError(401), "configuration_error"),
            (ProviderError(403), "configuration_error"),
            (ProviderError(402), "provider_unavailable"),
            (ProviderError(429), "rate_limited_or_busy"),
            (ProviderError(500), "rate_limited_or_busy"),
            (ProviderError(400), "criterion_failed"),
            (ProviderError(404), "criterion_failed"),
            (type("APITimeoutError", (Exception,), {})(), "timeout"),
            (type("APIConnectionError", (Exception,), {})(), "network_error"),
            (json.JSONDecodeError("bad", "x", 0), "invalid_response_format"),
            (ai.AIServiceError("empty"), "invalid_response_format"),
        ):
            with self.subTest(category=category):
                ai._provider_response_telemetry.set({})
                self.assertEqual(ai._evidence_error_category(error), category)

        ai._provider_response_telemetry.set({"finish_reason": "length"})
        self.assertEqual(ai._evidence_error_category(json.JSONDecodeError("bad", "x", 0)), "output_too_long")

    def test_matcher_failure_paths_keep_existing_application_retry_counts(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Synthetic fact."}]'
        model = '{"criteria":[{"criteria_id":"C1","criteria_text":"Synthetic criterion"}]}'

        cases = (
            ("invalid_json", json.JSONDecodeError("bad", "x", 0), 3, "invalid_response_format"),
            ("top_level_array", ValueError("not an object"), 1, "invalid_response_format"),
            ("empty", ai.AIServiceError("empty"), 1, "invalid_response_format"),
            ("configuration", RuntimeError("OPENAI_API_KEY is not configured."), 1, "configuration_error"),
        )
        for label, error, expected_calls, category in cases:
            with self.subTest(label=label):
                ai._provider_response_telemetry.set({})
                with patch.object(ai.settings, "ai_provider", "openai"), patch.object(
                    ai, "_openai_draft", side_effect=error,
                ) as provider, self.assertRaises(ai.EvidenceMatchDiagnosticError) as raised:
                    ai.match_evidence_batch(ckb, model, run_id=f"run-{label}")
                self.assertEqual(provider.call_count, expected_calls)
                self.assertEqual(raised.exception.diagnostic_category, category)

    def test_length_failure_does_not_repeat_an_unsplittable_request(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Synthetic fact."}]'
        model = '{"criteria":[{"criteria_id":"C1","criteria_text":"Synthetic criterion"}]}'

        def truncated(_prompt):
            ai._provider_response_telemetry.set({"finish_reason": "length", "completion_tokens": 4000})
            return "not-json"

        with patch.object(ai.settings, "ai_provider", "openai"), patch.object(
            ai, "_openai_draft", side_effect=truncated,
        ) as provider, self.assertRaises(ai.EvidenceMatchDiagnosticError) as raised:
            ai.match_evidence_batch(ckb, model, run_id="run-length")

        self.assertEqual(provider.call_count, 1)
        self.assertEqual(raised.exception.diagnostic_category, "output_too_long")

    def test_provider_usage_and_response_ids_are_logged_without_response_content(self):
        response = SimpleNamespace(
            id="response-123", _request_id="request-456", model="synthetic-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, prompt_cache_hit_tokens=8,
                prompt_cache_miss_tokens=2, completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
            ),
        )
        with patch.object(ai.settings, "deepseek_api_key", "synthetic-key"), patch.object(ai, "OpenAI") as client:
            client.return_value.chat.completions.create.return_value = response
            ai._deepseek_draft("PRIVATE_PROMPT_FIXTURE")
        telemetry = ai.provider_response_telemetry()
        self.assertEqual((telemetry["prompt_tokens"], telemetry["completion_tokens"]), (10, 5))
        self.assertEqual((telemetry["cache_hit_tokens"], telemetry["cache_miss_tokens"]), (8, 2))
        self.assertEqual((telemetry["reasoning_tokens"], telemetry["response_id"]), (1, "response-123"))
        self.assertEqual(telemetry["provider_request_id"], "request-456")
        self.assertNotIn("PRIVATE_PROMPT_FIXTURE", json.dumps(telemetry))

    def test_error_categories_return_safe_status_and_reference(self):
        cases = [
            (SimpleNamespace(diagnostic_category="timeout"), 504, "timed out"),
            (SimpleNamespace(diagnostic_category="output_too_long"), 502, "safe output limit"),
            (SimpleNamespace(diagnostic_category="invalid_response_format"), 502, "invalid evidence-matching response"),
            (SimpleNamespace(diagnostic_category="configuration_error"), 503, "not configured correctly"),
            (SimpleNamespace(diagnostic_category="rate_limited_or_busy"), 503, "temporarily busy"),
            (SimpleNamespace(diagnostic_category="network_error"), 503, "Wait a few minutes"),
            (SimpleNamespace(diagnostic_category="provider_unavailable"), 503, "account or service is unavailable"),
            (SimpleNamespace(diagnostic_category="criterion_failed"), 502, "all job criteria"),
            (SimpleNamespace(diagnostic_category="diagnosis_request_budget_exceeded"), 503, "more AI requests than allowed"),
            (SimpleNamespace(diagnostic_category="provider_circuit_open"), 503, "repeated AI provider failures"),
        ]
        for error, expected_status, phrase in cases:
            with self.subTest(category=error.diagnostic_category):
                status, detail = ai.evidence_error_detail(error, "12345678-rest")
                self.assertEqual(status, expected_status)
                self.assertEqual(detail["code"], error.diagnostic_category)
                self.assertIn(phrase, detail["message"])
                self.assertIn("No Pack credit was charged.", detail["message"])
                self.assertTrue(detail["message"].endswith("Ref: 12345678"))

    def test_request_budget_blocks_before_transport_and_caps_timeout_to_deadline(self):
        clock = iter((100.0, 100.0, 100.0, 100.0)).__next__
        state = ai.EvidenceMatchRunState(criterion_count=1, run_id="run-budget", clock=clock, deadline_seconds=5)
        seen = []
        transport = ai.EvidenceAttemptTransport(httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200, request=request)))
        token = ai.begin_evidence_provider_attempts({"run_state": state})
        try:
            with httpx.Client(transport=transport, timeout=90) as client:
                client.get("https://synthetic.invalid")
                self.assertEqual(seen[0].extensions["timeout"]["read"], 10.0)
                state.request_budget = 1
                with self.assertRaises(ai.EvidenceMatchDiagnosticError) as raised:
                    client.get("https://synthetic.invalid")
        finally:
            ai.end_evidence_provider_attempts(token)
        self.assertEqual(raised.exception.diagnostic_category, "diagnosis_request_budget_exceeded")
        self.assertEqual(len(seen), 1)

    def test_transport_timeout_extension_tolerates_none_values(self):
        clock = iter((100.0, 100.0)).__next__
        state = ai.EvidenceMatchRunState(criterion_count=1, run_id="run-none-timeout", clock=clock)
        seen = []
        transport = ai.EvidenceAttemptTransport(
            httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200, request=request)),
        )
        request = httpx.Request("GET", "https://synthetic.invalid")
        request.extensions["timeout"] = {"connect": None, "read": 90.0}
        token = ai.begin_evidence_provider_attempts({"run_state": state})
        try:
            response = transport.handle_request(request)
        finally:
            ai.end_evidence_provider_attempts(token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen[0].extensions["timeout"], {"connect": None, "read": 90.0})

    def test_deadline_is_checked_again_before_sdk_retry(self):
        times = iter((0.0, 0.0, 121.0)).__next__
        state = ai.EvidenceMatchRunState(criterion_count=1, run_id="run-deadline", clock=times)
        seen = []

        def busy(request):
            seen.append(request)
            return httpx.Response(500, request=request, json={"error": {"message": "synthetic busy"}})

        client = ai.DefaultHttpxClient(transport=httpx.MockTransport(busy))
        token = ai.begin_evidence_provider_attempts({"run_state": state})
        try:
            provider = ai.OpenAI(
                api_key="synthetic-key", base_url="https://synthetic.invalid",
                max_retries=1, http_client=ai.instrument_default_http_client(client),
            )
            with self.assertRaises(ai.OpenAIError) as raised:
                provider.chat.completions.create(
                    model="synthetic-model", messages=[{"role": "user", "content": "synthetic"}],
                )
        finally:
            ai.end_evidence_provider_attempts(token)
            client.close()

        self.assertEqual(raised.exception.__cause__.diagnostic_category, "timeout")
        self.assertEqual(len(seen), 1)

    def test_diagnosis_route_keeps_safe_detail_shape_and_logs_completion(self):
        application = SimpleNamespace(id=7, job_model_json='{"criteria":[{"criteria_id":"C1"}]}')
        session = SimpleNamespace(exec=lambda _query: SimpleNamespace(first=lambda: None))
        with patch.object(main, "get_for_user", return_value=application), patch.object(
            main, "application_master_resume", return_value=SimpleNamespace(id=1),
        ), patch.object(
            main, "prepare_application_decision",
            side_effect=ai.EvidenceMatchDiagnosticError("timeout", "ignored", "C1"),
        ), self.assertRaises(HTTPException) as raised:
            main.diagnose_application(7, session=session, user_id=None)

        self.assertEqual(raised.exception.status_code, 504)
        self.assertEqual(raised.exception.detail["code"], "timeout")
        self.assertRegex(raised.exception.detail["message"], r"Ref: [0-9a-f]{8}$")
        completed = [event for event in self.events() if event["event"] == "application_diagnosis_complete"]
        self.assertEqual((completed[-1]["application_id"], completed[-1]["failure_category"]), (7, "timeout"))
        self.assertEqual(len(completed[-1]["run_id"]), 36)

    def test_non_match_value_error_keeps_original_400_detail(self):
        application = SimpleNamespace(id=8, job_model_json="{}")
        session = SimpleNamespace(exec=lambda _query: SimpleNamespace(first=lambda: None))
        with patch.object(main, "get_for_user", return_value=application), patch.object(
            main, "application_master_resume", return_value=SimpleNamespace(id=1),
        ), patch.object(
            main, "prepare_application_decision", side_effect=ValueError("Synthetic decision input is invalid."),
        ), self.assertRaises(HTTPException) as raised:
            main.diagnose_application(8, session=session, user_id=None)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "Synthetic decision input is invalid.")
        completed = [event for event in self.events() if event["event"] == "application_diagnosis_complete"]
        self.assertEqual(completed[-1]["failure_category"], "input_error")

    def test_non_match_ai_service_error_keeps_original_502_detail(self):
        application = SimpleNamespace(id=9, job_model_json="{}")
        session = SimpleNamespace(exec=lambda _query: SimpleNamespace(first=lambda: None))
        with patch.object(main, "get_for_user", return_value=application), patch.object(
            main, "application_master_resume", return_value=SimpleNamespace(id=1),
        ), patch.object(
            main, "prepare_application_decision", side_effect=ai.AIServiceError("Synthetic downstream failure."),
        ), self.assertRaises(HTTPException) as raised:
            main.diagnose_application(9, session=session, user_id=None)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.detail, "Synthetic downstream failure.")
        completed = [event for event in self.events() if event["event"] == "application_diagnosis_complete"]
        self.assertEqual(completed[-1]["failure_category"], "ai_service_error")

    def test_non_object_job_model_does_not_mask_original_input_error(self):
        session = SimpleNamespace(exec=lambda _query: SimpleNamespace(first=lambda: None))
        for application_id, job_model_json in enumerate(("[]", '"x"', "null", "123"), start=10):
            with self.subTest(job_model_json=job_model_json):
                application = SimpleNamespace(id=application_id, job_model_json=job_model_json)
                with patch.object(main, "get_for_user", return_value=application), patch.object(
                    main, "application_master_resume", return_value=SimpleNamespace(id=1),
                ), patch.object(
                    main, "prepare_application_decision", side_effect=ValueError("Synthetic invalid decision."),
                ), self.assertRaises(HTTPException) as raised:
                    main.diagnose_application(application_id, session=session, user_id=None)

                self.assertEqual(raised.exception.status_code, 400)
                self.assertEqual(raised.exception.detail, "Synthetic invalid decision.")

    def test_completion_log_failure_does_not_mask_original_http_error(self):
        application = SimpleNamespace(id=14, job_model_json="{}")
        session = SimpleNamespace(exec=lambda _query: SimpleNamespace(first=lambda: None))
        with patch.object(main, "get_for_user", return_value=application), patch.object(
            main, "application_master_resume", return_value=SimpleNamespace(id=1),
        ), patch.object(
            main, "prepare_application_decision", side_effect=ValueError("Synthetic original error."),
        ), patch.object(
            main, "safe_evidence_log", side_effect=RuntimeError("Synthetic logging failure."),
        ), self.assertRaises(HTTPException) as raised:
            main.diagnose_application(14, session=session, user_id=None)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "Synthetic original error.")

    def test_match_result_and_application_attempt_count_are_unchanged_with_diagnostics(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Synthetic reporting fact."}]'
        model = '{"criteria":[{"criteria_id":"C1","criteria_text":"Synthetic reporting"}]}'
        invalid = "not-json"
        valid = '{"criteria_id":"C1","evidence_verdicts":[{"evidence_id":"EV001","verdict":"direct","support_quote":"Synthetic reporting fact.","matched_requirement_terms":["reporting"]}]}'
        with patch.object(ai.settings, "ai_provider", "openai"), patch.object(
            ai, "_openai_draft", side_effect=[invalid, valid]
        ) as provider:
            result = ai.match_evidence_batch(ckb, model, run_id="run-match", application_id=9)

        self.assertEqual(provider.call_count, 2)
        self.assertEqual(result["matches"][0]["matched_evidence"], ["EV001"])
        complete = [event for event in self.events() if event.get("event") == "evidence_match_criterion_complete"]
        self.assertEqual(complete[0]["application_attempts"], 2)


if __name__ == "__main__":
    unittest.main()
