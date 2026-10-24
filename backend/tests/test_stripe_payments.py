import unittest
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.auth import AuthenticatedUser, get_authenticated_user
from app.config import Settings, settings
from app.database import get_session
from app.main import app
from app.models import PaymentCheckoutRate, Purchase, StripeEvent
from app.payments import get_stripe_gateway


class StripePaymentCatalogTests(unittest.TestCase):
    def test_gst_defaults_off_and_keeps_catalog_prices_unchanged(self):
        from app.payments import build_pack_catalog

        catalog = build_pack_catalog(gst_enabled=False)

        self.assertEqual(
            {
                code: (item.subtotal_cents, item.gst_cents, item.total_cents, item.single_pack_price_cents)
                for code, item in catalog.items()
            },
            {
                "single": (1695, 0, 1695, 1695),
                "starter": (10995, 0, 10995, 1695),
                "job_search": (19900, 0, 19900, 1695),
            },
        )

    def test_gst_enabled_rounds_once_per_order_half_up(self):
        from app.payments import build_pack_catalog

        catalog = build_pack_catalog(gst_enabled=True)

        self.assertEqual(
            {
                code: (item.gst_cents, item.total_cents, item.single_pack_price_cents)
                for code, item in catalog.items()
            },
            {
                "single": (170, 1865, 1865),
                "starter": (1100, 12095, 1865),
                "job_search": (1990, 21890, 1865),
            },
        )

    def test_custom_and_unknown_packages_are_not_purchasable(self):
        from app.payments import purchasable_package

        for package_code in ("custom", "unknown"):
            with self.subTest(package_code=package_code), self.assertRaises(ValueError):
                purchasable_package(package_code, gst_enabled=False)

    def test_stripe_settings_default_to_test_mode_with_gst_off(self):
        configured = Settings(_env_file=None)

        self.assertEqual(configured.stripe_mode, "test")
        self.assertFalse(configured.stripe_gst_enabled)

    def test_authenticated_identity_uses_verified_claim_email(self):
        user_id = uuid4()
        with patch("app.auth._verified_claims", return_value={
            "sub": str(user_id), "email": "verified@example.test", "role": "authenticated",
        }):
            identity = get_authenticated_user("Bearer signed-token")

        self.assertEqual((identity.id, identity.email), (user_id, "verified@example.test"))

    def test_stripe_settings_accept_matching_secret_and_restricted_keys(self):
        from app.payments import validate_stripe_settings

        for mode, key in (
            ("test", "sk_test_fake"),
            ("test", "rk_test_fake"),
            ("live", "sk_live_fake"),
            ("live", "rk_live_fake"),
        ):
            with self.subTest(mode=mode, key=key):
                validate_stripe_settings(Settings(
                    _env_file=None,
                    stripe_mode=mode,
                    stripe_secret_key=key,
                    stripe_webhook_secret="whsec_fake",
                ))

    def test_stripe_settings_reject_mode_key_mismatch(self):
        from app.payments import validate_stripe_settings

        for mode, key in (
            ("test", "sk_live_fake"),
            ("test", "rk_live_fake"),
            ("live", "sk_test_fake"),
            ("live", "rk_test_fake"),
        ):
            with self.subTest(mode=mode, key=key), self.assertRaises(ValueError):
                validate_stripe_settings(Settings(
                    _env_file=None,
                    stripe_mode=mode,
                    stripe_secret_key=key,
                    stripe_webhook_secret="whsec_fake",
                ))


class CheckoutSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.user = AuthenticatedUser(uuid4(), "buyer@example.test")
        self.gateway = unittest.mock.Mock()
        self.gateway.create_checkout_session.return_value = SimpleNamespace(
            id="cs_test_one", url="https://checkout.stripe.test/one", livemode=False,
        )
        self.gateway.retrieve_checkout_session.return_value = self.gateway.create_checkout_session.return_value

        def sessions():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = sessions
        app.dependency_overrides[get_authenticated_user] = lambda: self.user
        app.dependency_overrides[get_stripe_gateway] = lambda: self.gateway
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def test_checkout_uses_server_price_dashboard_methods_and_verified_identity(self):
        response = self.client.post(
            "/payments/checkout-sessions",
            headers={"Idempotency-Key": "attempt-one"},
            json={"package_code": "single"},
        )

        self.assertEqual(response.status_code, 200)
        params, _idempotency_key = self.gateway.create_checkout_session.call_args.args
        self.assertNotIn("payment_method_types", params)
        self.assertNotIn("allowed_payment_method_types", params)
        self.assertEqual(params["line_items"][0]["price_data"]["unit_amount"], 1695)
        self.assertEqual(params["customer_email"], "buyer@example.test")
        self.assertEqual(params["metadata"]["user_id"], str(self.user.id))
        with Session(self.engine) as session:
            order = session.exec(select(Purchase)).one()
        self.assertEqual((order.total_paid_cents, order.gst_cents, order.single_pack_price_cents), (1695, 0, 1695))

    def test_checkout_parameters_match_pinned_sdk_and_wire_version(self):
        import stripe
        from stripe.params.checkout import (
            SessionCreateParams, SessionCreateParamsLineItem,
            SessionCreateParamsLineItemPriceData, SessionCreateParamsLineItemPriceDataProductData,
        )
        from urllib.parse import parse_qs
        from app.payments import StripeGateway, STRIPE_API_VERSION

        gateway = StripeGateway("sk_test_contract_only")
        response_body = json.dumps({
            "id": "cs_test_contract", "object": "checkout.session",
            "url": "https://checkout.stripe.test/contract", "livemode": False,
        })
        # Exercise the real SDK; intercept only HTTP, never contact Stripe.
        with patch("stripe._http_client.RequestsClient.request", return_value=(
            response_body, 200, {},
        )) as request:
            app.dependency_overrides[get_stripe_gateway] = lambda: gateway
            response = self.client.post(
                "/payments/checkout-sessions", headers={"Idempotency-Key": "contract"},
                json={"package_code": "single"},
            )
        self.assertEqual(response.status_code, 200)
        args, kwargs = request.call_args
        headers = args[2] if len(args) > 2 else kwargs["headers"]
        body = args[3] if len(args) > 3 else kwargs["post_data"]
        fields = parse_qs(body)
        self.assertEqual(headers["Stripe-Version"], STRIPE_API_VERSION)
        self.assertTrue(headers["Idempotency-Key"].startswith("checkout:"))
        self.assertEqual(args[0], "post")
        self.assertTrue(args[1].endswith("/v1/checkout/sessions"))
        self.assertEqual(fields["line_items[0][price_data][currency]"], ["aud"])
        self.assertEqual(fields["line_items[0][price_data][unit_amount]"], ["1695"])
        self.assertEqual(fields["line_items[0][quantity]"], ["1"])
        self.assertEqual(fields["line_items[0][price_data][product_data][name]"], ["single credit pack"])
        self.assertLessEqual(
            {key.split("[")[0] for key in fields},
            set(SessionCreateParams.__annotations__),
        )
        for prefix, schema in (
            ("line_items[0][", SessionCreateParamsLineItem),
            ("line_items[0][price_data][", SessionCreateParamsLineItemPriceData),
            ("line_items[0][price_data][product_data][", SessionCreateParamsLineItemPriceDataProductData),
        ):
            self.assertLessEqual(
                {key[len(prefix):].split("]")[0] for key in fields if key.startswith(prefix)},
                set(schema.__annotations__),
            )
        self.assertEqual(stripe._api_version._ApiVersion.CURRENT, STRIPE_API_VERSION)

    def test_invalid_request_leaves_no_purchase_and_new_key_retries_once(self):
        import stripe
        self.gateway.create_checkout_session.side_effect = stripe.InvalidRequestError(
            "payment_method_types is no longer supported", "payment_method_types",
        )
        with self.assertRaises(stripe.InvalidRequestError):
            self.client.post(
                "/payments/checkout-sessions", headers={"Idempotency-Key": "invalid"},
                json={"package_code": "single"},
            )
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(Purchase)).all(), [])
            reservation = session.exec(select(PaymentCheckoutRate)).one()
            self.assertEqual(reservation.status, "released")
            self.assertIsNotNone(reservation.released_at)
            self.assertIsNone(reservation.stripe_checkout_session_id)
        self.gateway.create_checkout_session.side_effect = None
        headers = {"Idempotency-Key": "fixed-new-key"}
        for _ in range(2):
            response = self.client.post(
                "/payments/checkout-sessions", headers=headers, json={"package_code": "single"},
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.gateway.create_checkout_session.call_count, 2)
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(Purchase)).all()), 1)
            self.assertEqual(sorted(row.status for row in session.exec(select(PaymentCheckoutRate))),
                             ["completed", "released"])

    def test_checkout_rejects_client_owned_payment_fields(self):
        response = self.client.post(
            "/payments/checkout-sessions",
            headers={"Idempotency-Key": "attempt-two"},
            json={"package_code": "single", "user_id": str(uuid4()), "amount_cents": 1},
        )

        self.assertEqual(response.status_code, 422)
        self.gateway.create_checkout_session.assert_not_called()

    def test_same_key_with_different_package_returns_409_without_second_create(self):
        headers = {"Idempotency-Key": "same-key"}
        first = self.client.post("/payments/checkout-sessions", headers=headers, json={"package_code": "single"})
        second = self.client.post("/payments/checkout-sessions", headers=headers, json={"package_code": "starter"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.gateway.create_checkout_session.call_count, 1)

    def test_stripe_create_failure_releases_rate_slot(self):
        self.gateway.create_checkout_session.side_effect = RuntimeError("Stripe unavailable")
        with self.assertRaises(RuntimeError):
            self.client.post(
                "/payments/checkout-sessions",
                headers={"Idempotency-Key": "failed-create"},
                json={"package_code": "single"},
            )

        with Session(self.engine) as session:
            reservation = session.exec(select(PaymentCheckoutRate)).one()
        self.assertEqual(reservation.status, "released")

    def test_sixth_unique_checkout_is_rate_limited(self):
        for index in range(5):
            self.gateway.create_checkout_session.return_value = SimpleNamespace(
                id=f"cs_test_{index}", url=f"https://checkout.stripe.test/{index}", livemode=False,
            )
            response = self.client.post(
                "/payments/checkout-sessions",
                headers={"Idempotency-Key": f"attempt-{index}"},
                json={"package_code": "single"},
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/payments/checkout-sessions",
            headers={"Idempotency-Key": "attempt-six"},
            json={"package_code": "single"},
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "600")
        self.assertEqual(self.gateway.create_checkout_session.call_count, 5)

    def test_signed_completed_webhook_grants_once(self):
        with Session(self.engine) as session:
            session.add(Purchase(
                user_id=self.user.id, stripe_checkout_session_id="cs_paid", status="pending",
                package_code="single", currency="AUD", credits=1, amount_cents=1695,
                subtotal_cents=1695, gst_cents=0, total_paid_cents=1695,
                single_pack_price_cents=1695, livemode=False,
            ))
            session.commit()
        event = {
            "id": "evt_paid", "type": "checkout.session.completed", "livemode": False,
            "data": {"object": {
                "id": "cs_paid", "object": "checkout.session", "mode": "payment",
                "payment_status": "paid", "payment_intent": "pi_paid",
                "amount_total": 1695, "currency": "aud",
                "metadata": {
                    "user_id": str(self.user.id), "package_code": "single", "credits": "1",
                    "gst_enabled": "false", "catalog_version": "2026-10-09",
                },
            }},
        }
        payload = json.dumps(event, separators=(",", ":")).encode()
        timestamp, secret = int(time.time()), "whsec_test_fixture"
        signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
        with patch.object(settings, "stripe_webhook_secret", secret), patch.object(settings, "stripe_mode", "test"):
            first = self.client.post(
                "/payments/stripe/webhook", content=payload,
                headers={"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"},
            )
            second = self.client.post(
                "/payments/stripe/webhook", content=payload,
                headers={"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"},
            )
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        with Session(self.engine) as session:
            order = session.exec(select(Purchase).where(Purchase.stripe_checkout_session_id == "cs_paid")).one()
        self.assertEqual(order.status, "paid")

    def test_dashboard_async_payment_waits_for_paid_event_and_grants_once(self):
        self.client.post(
            "/payments/checkout-sessions", headers={"Idempotency-Key": "async"},
            json={"package_code": "single"},
        )
        params = self.gateway.create_checkout_session.call_args.args[0]
        event = {
            "id": "evt_async_unpaid", "object": "event", "api_version": "2026-09-30.endive",
            "type": "checkout.session.completed", "livemode": False,
            "data": {"object": {
                "id": "cs_test_one", "object": "checkout.session", "mode": "payment",
                "payment_status": "unpaid", "payment_intent": "pi_async",
                "amount_total": 1695, "currency": "aud", "metadata": params["metadata"],
            }},
        }
        self.assertEqual(self._post_signed_event(event).status_code, 200)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(Purchase)).one().status, "pending")
        event["id"], event["type"] = "evt_async_paid", "checkout.session.async_payment_succeeded"
        event["data"]["object"]["payment_status"] = "paid"
        for _ in range(2):
            self.assertEqual(self._post_signed_event(event).status_code, 200)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(Purchase)).one().status, "paid")
            from app.models import PackCreditLedger
            grants = session.exec(select(PackCreditLedger).where(
                PackCreditLedger.entry_type == "grant_stripe_purchase",
            )).all()
            self.assertEqual(len(grants), 1)

    def test_bad_signature_and_oversized_body_are_rejected(self):
        with patch.object(settings, "stripe_webhook_secret", "whsec_test_fixture"):
            bad = self.client.post(
                "/payments/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=1,v1=bad"},
            )
            oversized = self.client.post(
                "/payments/stripe/webhook", content=b"x" * (256 * 1024 + 1),
                headers={"Stripe-Signature": "t=1,v1=bad"},
            )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(oversized.status_code, 413)

    def test_livemode_conflict_is_recorded_failed_and_acknowledged(self):
        with Session(self.engine) as session:
            session.add(Purchase(
                user_id=self.user.id, stripe_checkout_session_id="cs_wrong_mode", status="pending",
                package_code="single", currency="AUD", credits=1, amount_cents=1695,
                subtotal_cents=1695, gst_cents=0, total_paid_cents=1695,
                single_pack_price_cents=1695, livemode=False,
            ))
            session.commit()
        event = {
            "id": "evt_wrong_mode", "type": "checkout.session.completed", "livemode": True,
            "data": {"object": {
                "id": "cs_wrong_mode", "mode": "payment", "payment_status": "paid",
                "payment_intent": "pi_wrong_mode", "amount_total": 1695, "currency": "aud",
                "metadata": {"user_id": str(self.user.id)},
            }},
        }
        payload = json.dumps(event, separators=(",", ":")).encode()
        timestamp, secret = int(time.time()), "whsec_test_fixture"
        signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
        with patch.object(settings, "stripe_webhook_secret", secret), patch.object(settings, "stripe_mode", "test"):
            response = self.client.post(
                "/payments/stripe/webhook", content=payload,
                headers={"Stripe-Signature": f"t={timestamp},v1={signature}"},
            )
        self.assertEqual(response.status_code, 200)
        with Session(self.engine) as session:
            recorded = session.get(StripeEvent, "evt_wrong_mode")
            order = session.exec(select(Purchase).where(Purchase.stripe_checkout_session_id == "cs_wrong_mode")).one()
        self.assertEqual((recorded.status, recorded.failure_reason_code), ("failed", "livemode_mismatch"))
        self.assertEqual(order.status, "pending")

    def _post_signed_event(self, event):
        payload = json.dumps(event, separators=(",", ":")).encode()
        timestamp, secret = int(time.time()), "whsec_test_fixture"
        signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
        with patch.object(settings, "stripe_webhook_secret", secret):
            return self.client.post(
                "/payments/stripe/webhook", content=payload,
                headers={"Stripe-Signature": f"t={timestamp},v1={signature}"},
            )

    def test_async_failure_only_downgrades_pending_order(self):
        with Session(self.engine) as session:
            session.add_all([
                Purchase(
                    user_id=self.user.id, stripe_checkout_session_id="cs_pending", status="pending",
                    package_code="single", currency="AUD", credits=1, amount_cents=1695,
                    subtotal_cents=1695, gst_cents=0, total_paid_cents=1695, single_pack_price_cents=1695,
                ),
                Purchase(
                    user_id=self.user.id, stripe_checkout_session_id="cs_paid_terminal", status="paid",
                    package_code="single", currency="AUD", credits=1, amount_cents=1695,
                    subtotal_cents=1695, gst_cents=0, total_paid_cents=1695, single_pack_price_cents=1695,
                ),
            ])
            session.commit()
        for suffix in ("pending", "paid_terminal"):
            response = self._post_signed_event({
                "id": f"evt_failed_{suffix}", "type": "checkout.session.async_payment_failed", "livemode": False,
                "data": {"object": {"id": f"cs_{suffix}", "object": "checkout.session"}},
            })
            self.assertEqual(response.status_code, 200)
        with Session(self.engine) as session:
            states = {row.stripe_checkout_session_id: row.status for row in session.exec(select(Purchase)).all()}
        self.assertEqual(states["cs_pending"], "failed")
        self.assertEqual(states["cs_paid_terminal"], "paid")

    def test_refund_is_observed_pending_and_does_not_change_paid_state(self):
        with Session(self.engine) as session:
            session.add(Purchase(
                user_id=self.user.id, stripe_checkout_session_id="cs_refund", stripe_payment_intent_id="pi_refund",
                stripe_charge_id="ch_refund", status="paid", package_code="single", currency="AUD",
                credits=1, amount_cents=1695, subtotal_cents=1695, gst_cents=0,
                total_paid_cents=1695, single_pack_price_cents=1695,
            ))
            session.commit()
        response = self._post_signed_event({
            "id": "evt_refund", "type": "charge.refunded", "livemode": False,
            "data": {"object": {"id": "ch_refund", "object": "charge", "payment_intent": "pi_refund"}},
        })
        self.assertEqual(response.status_code, 200)
        with Session(self.engine) as session:
            event = session.get(StripeEvent, "evt_refund")
            order = session.exec(select(Purchase).where(Purchase.stripe_checkout_session_id == "cs_refund")).one()
        self.assertEqual(event.status, "observed_pending")
        self.assertIsNotNone(order.refund_detected_at)
        self.assertEqual(order.status, "paid")

    def test_stripe_settings_require_webhook_secret_when_key_is_configured(self):
        from app.payments import validate_stripe_settings

        configured = Settings(_env_file=None, stripe_secret_key="sk_test_fake")
        with self.assertRaises(ValueError):
            validate_stripe_settings(configured)


if __name__ == "__main__":
    unittest.main()
