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

    def test_stripe_settings_reject_mode_key_mismatch(self):
        from app.payments import validate_stripe_settings

        configured = Settings(
            _env_file=None,
            stripe_mode="live",
            stripe_secret_key="sk_test_fake",
            stripe_webhook_secret="whsec_fake",
        )
        with self.assertRaises(ValueError):
            validate_stripe_settings(configured)


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

    def test_checkout_uses_server_price_card_and_verified_identity(self):
        response = self.client.post(
            "/payments/checkout-sessions",
            headers={"Idempotency-Key": "attempt-one"},
            json={"package_code": "single"},
        )

        self.assertEqual(response.status_code, 200)
        params, _idempotency_key = self.gateway.create_checkout_session.call_args.args
        self.assertEqual(params["payment_method_types"], ["card"])
        self.assertEqual(params["line_items"][0]["price_data"]["unit_amount"], 1695)
        self.assertEqual(params["customer_email"], "buyer@example.test")
        self.assertEqual(params["metadata"]["user_id"], str(self.user.id))
        with Session(self.engine) as session:
            order = session.exec(select(Purchase)).one()
        self.assertEqual((order.total_paid_cents, order.gst_cents, order.single_pack_price_cents), (1695, 0, 1695))

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
                "metadata": {"user_id": str(self.user.id), "package_code": "single"},
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

    def test_stripe_settings_require_webhook_secret_when_key_is_configured(self):
        from app.payments import validate_stripe_settings

        configured = Settings(_env_file=None, stripe_secret_key="sk_test_fake")
        with self.assertRaises(ValueError):
            validate_stripe_settings(configured)


if __name__ == "__main__":
    unittest.main()
