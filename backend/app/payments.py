from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlmodel import Session, func, select

from .auth import AuthenticatedUser
from .config import settings
from .models import PaymentCheckoutRate, Purchase, StripeEvent
from .pack_credits import process_stripe_purchase_event, record_stripe_event_failure


STRIPE_API_VERSION = "2026-09-30.endive"
CATALOG_VERSION = "2026-10-09"

_BASE_PACKAGES = {
    "single": (1, 1695),
    "starter": (8, 10995),
    "job_search": (18, 19900),
}


@dataclass(frozen=True)
class PaymentPackage:
    code: str
    credits: int
    subtotal_cents: int
    gst_cents: int
    total_cents: int
    currency: str
    single_pack_price_cents: int
    catalog_version: str


def _gst_cents(subtotal_cents: int, enabled: bool) -> int:
    if not enabled:
        return 0
    return int((Decimal(subtotal_cents) * Decimal("0.10")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def build_pack_catalog(gst_enabled: bool) -> dict[str, PaymentPackage]:
    single_gst = _gst_cents(_BASE_PACKAGES["single"][1], gst_enabled)
    single_total = _BASE_PACKAGES["single"][1] + single_gst
    return {
        code: PaymentPackage(
            code=code,
            credits=credits,
            subtotal_cents=subtotal,
            gst_cents=(gst := _gst_cents(subtotal, gst_enabled)),
            total_cents=subtotal + gst,
            currency="AUD",
            single_pack_price_cents=single_total,
            catalog_version=CATALOG_VERSION,
        )
        for code, (credits, subtotal) in _BASE_PACKAGES.items()
    }


def purchasable_package(package_code: str, gst_enabled: bool | None = None) -> PaymentPackage:
    catalog = build_pack_catalog(settings.stripe_gst_enabled if gst_enabled is None else gst_enabled)
    try:
        return catalog[package_code]
    except KeyError as error:
        raise ValueError("Unknown purchasable package") from error


def validate_stripe_settings(configured=settings) -> None:
    mode = configured.stripe_mode.strip().lower()
    if mode not in {"test", "live"}:
        raise ValueError("STRIPE_MODE must be test or live")
    if configured.stripe_api_version != STRIPE_API_VERSION:
        raise ValueError(f"STRIPE_API_VERSION must be {STRIPE_API_VERSION}")
    key = configured.stripe_secret_key
    if not key and not configured.stripe_webhook_secret:
        return
    if not key or not configured.stripe_webhook_secret:
        raise ValueError("STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET must be configured together")
    expected_prefix = "sk_live_" if mode == "live" else "sk_test_"
    if not key.startswith(expected_prefix):
        raise ValueError("Stripe key does not match STRIPE_MODE")


class CheckoutSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_code: Literal["single", "starter", "job_search"]


class CheckoutSessionResponse(BaseModel):
    checkout_session_id: str
    checkout_url: str


class StripeGateway:
    def __init__(self, secret_key: str):
        import stripe
        self.client = stripe.StripeClient(secret_key, stripe_version=STRIPE_API_VERSION)

    def create_checkout_session(self, params: dict, idempotency_key: str):
        return self.client.v1.checkout.sessions.create(params, options={"idempotency_key": idempotency_key})

    def retrieve_checkout_session(self, session_id: str):
        return self.client.v1.checkout.sessions.retrieve(session_id)

    def retrieve_event(self, event_id: str):
        return self.client.v1.events.retrieve(event_id)


def get_stripe_gateway() -> StripeGateway:
    validate_stripe_settings()
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Payments are not configured.")
    return StripeGateway(settings.stripe_secret_key)


def _checkout_key(user_id, raw_key: str) -> str:
    if not raw_key or len(raw_key) > 255:
        raise HTTPException(400, "A valid Idempotency-Key is required.")
    return sha256(f"checkout:{user_id}:{raw_key}".encode()).hexdigest()


def create_checkout_session(
    session: Session,
    gateway,
    identity: AuthenticatedUser,
    package_code: str,
    raw_idempotency_key: str,
    now: datetime | None = None,
) -> CheckoutSessionResponse:
    now = now or datetime.now(timezone.utc)
    key_hash = _checkout_key(identity.id, raw_idempotency_key)
    if session.bind.dialect.name == "postgresql":
        reservation = session.execute(text("""
            select result, stripe_checkout_session_id
            from public.reserve_checkout_creation(:user_id, :key_hash, :package_code)
        """), {"user_id": identity.id, "key_hash": key_hash,
                "package_code": package_code}).one()
        session.commit()
        result, existing_session_id = reservation
        if result == "conflict":
            raise HTTPException(409, "Idempotency-Key is already used for another package.")
        if result == "rate_limited":
            raise HTTPException(429, "Too many checkout sessions.", headers={"Retry-After": "600"})
        if result == "existing":
            remote = gateway.retrieve_checkout_session(existing_session_id)
            return CheckoutSessionResponse(checkout_session_id=remote.id, checkout_url=remote.url)
        existing = session.exec(select(PaymentCheckoutRate).where(
            PaymentCheckoutRate.user_id == identity.id,
            PaymentCheckoutRate.idempotency_key_hash == key_hash,
        )).one()
    else:
        existing = session.exec(select(PaymentCheckoutRate).where(
            PaymentCheckoutRate.user_id == identity.id,
            PaymentCheckoutRate.idempotency_key_hash == key_hash,
        )).first()
        if existing:
            if existing.package_code != package_code:
                raise HTTPException(409, "Idempotency-Key is already used for another package.")
            if existing.stripe_checkout_session_id:
                remote = gateway.retrieve_checkout_session(existing.stripe_checkout_session_id)
                return CheckoutSessionResponse(checkout_session_id=remote.id, checkout_url=remote.url)
            if existing.status == "released":
                existing.status, existing.released_at = "reserved", None
            else:
                raise HTTPException(409, "Checkout creation is already in progress.")
        else:
            active = session.exec(select(func.count()).select_from(PaymentCheckoutRate).where(
                PaymentCheckoutRate.user_id == identity.id,
                PaymentCheckoutRate.created_at >= now - timedelta(minutes=10),
                PaymentCheckoutRate.status != "released",
            )).one()
            if active >= 5:
                raise HTTPException(429, "Too many checkout sessions.", headers={"Retry-After": "600"})
            existing = PaymentCheckoutRate(
                user_id=identity.id, idempotency_key_hash=key_hash, package_code=package_code, created_at=now,
            )
            session.add(existing)
        session.commit()

    package = purchasable_package(package_code)
    expires_at = now + timedelta(minutes=30)
    metadata = {
        "user_id": str(identity.id), "package_code": package.code,
        "credits": str(package.credits), "catalog_version": package.catalog_version,
        "gst_enabled": str(settings.stripe_gst_enabled).lower(),
    }
    params = {
        "mode": "payment",
        "payment_method_types": ["card"],
        "line_items": [{"quantity": 1, "price_data": {
            "currency": package.currency.lower(),
            "unit_amount": package.total_cents,
            "product_data": {"name": f"{package.code} credit pack"},
        }}],
        "metadata": metadata,
        "client_reference_id": str(identity.id),
        "success_url": settings.frontend_origin.rstrip("/") + "/payments/success?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": settings.frontend_origin.rstrip("/") + "/payments/cancelled",
        "expires_at": int(expires_at.timestamp()),
    }
    if identity.email:
        params["customer_email"] = identity.email
    try:
        remote = gateway.create_checkout_session(params, f"checkout:{key_hash}")
    except Exception:
        existing.status, existing.released_at = "released", datetime.now(timezone.utc)
        session.add(existing)
        session.commit()
        raise

    existing.status = "completed"
    existing.stripe_checkout_session_id = remote.id
    session.add(existing)
    session.add(Purchase(
        user_id=identity.id,
        stripe_checkout_session_id=remote.id,
        status="pending",
        package_code=package.code,
        currency=package.currency,
        credits=package.credits,
        amount_cents=package.total_cents,
        subtotal_cents=package.subtotal_cents,
        gst_cents=package.gst_cents,
        total_paid_cents=package.total_cents,
        single_pack_price_cents=package.single_pack_price_cents,
        gst_enabled=settings.stripe_gst_enabled,
        livemode=bool(remote.livemode),
        catalog_version=package.catalog_version,
        expires_at=expires_at,
        checkout_idempotency_key_hash=key_hash,
    ))
    session.commit()
    return CheckoutSessionResponse(checkout_session_id=remote.id, checkout_url=remote.url)


MAX_WEBHOOK_BYTES = 256 * 1024


class DeterministicPaymentConflict(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def construct_webhook_event(payload: bytes, signature: str, secret: str):
    import stripe
    stripe.Webhook.construct_event(payload, signature, secret, tolerance=300)
    return json.loads(payload)


def handle_payment_webhook(session: Session, event: dict) -> None:
    event_type = event.get("type")
    observed_types = {
        "refund.created", "refund.updated", "charge.refunded",
        "charge.dispute.created", "charge.dispute.updated", "charge.dispute.closed",
    }
    business_types = {
        "checkout.session.async_payment_failed", "checkout.session.expired",
    }
    if event_type in observed_types | business_types or (
        event_type == "checkout.session.completed"
        and event.get("data", {}).get("object", {}).get("payment_status") != "paid"
    ):
        record_payment_observation(session, event)
        return
    if event_type not in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        return
    obj = event["data"]["object"]
    if obj.get("payment_status") != "paid" or obj.get("mode") != "payment":
        return
    order = session.exec(select(Purchase).where(Purchase.stripe_checkout_session_id == obj.get("id"))).first()
    if not order:
        raise DeterministicPaymentConflict("session_not_found")
    event_livemode = bool(event.get("livemode"))
    configured_live = settings.stripe_mode.strip().lower() == "live"
    if event_livemode != configured_live or event_livemode != order.livemode:
        raise DeterministicPaymentConflict("livemode_mismatch")
    if str(obj.get("currency", "")).upper() != order.currency or obj.get("amount_total") != order.total_paid_cents:
        raise DeterministicPaymentConflict("amount_or_currency_mismatch")
    metadata = obj.get("metadata", {})
    if metadata.get("user_id") != str(order.user_id):
        raise DeterministicPaymentConflict("user_mismatch")
    expected_metadata = {
        "package_code": order.package_code,
        "credits": str(order.credits),
        "gst_enabled": str(order.gst_enabled).lower(),
        "catalog_version": order.catalog_version,
    }
    if any(metadata.get(key) != value for key, value in expected_metadata.items()):
        raise DeterministicPaymentConflict("catalog_facts_mismatch")
    if not obj.get("payment_intent"):
        raise DeterministicPaymentConflict("payment_intent_missing")
    process_stripe_purchase_event(
        session,
        stripe_event_id=event["id"], stripe_event_type=event_type, livemode=event_livemode,
        stripe_checkout_session_id=order.stripe_checkout_session_id,
        stripe_payment_intent_id=obj["payment_intent"], user_id=order.user_id,
        package_code=order.package_code, credits=order.credits,
        subtotal_cents=order.subtotal_cents, gst_cents=order.gst_cents,
        total_paid_cents=order.total_paid_cents,
        single_pack_price_cents=order.single_pack_price_cents, currency=order.currency,
        gst_enabled=order.gst_enabled, catalog_version=order.catalog_version,
    )


def record_payment_observation(session: Session, event: dict) -> None:
    event_type = event["type"]
    obj = event["data"]["object"]
    session_id = obj.get("id") if event_type.startswith("checkout.session.") else None
    order = session.exec(select(Purchase).where(Purchase.stripe_checkout_session_id == session_id)).first() if session_id else None
    if not order and obj.get("payment_intent"):
        order = session.exec(select(Purchase).where(Purchase.stripe_payment_intent_id == obj["payment_intent"])).first()
    if not order and obj.get("charge"):
        order = session.exec(select(Purchase).where(Purchase.stripe_charge_id == obj["charge"])).first()

    event_livemode = bool(event.get("livemode"))
    configured_live = settings.stripe_mode.strip().lower() == "live"
    if event_livemode != configured_live or (order and event_livemode != order.livemode):
        raise DeterministicPaymentConflict("livemode_mismatch")

    observed_pending = event_type.startswith("refund.") or event_type.startswith("charge.")
    status = "observed_pending" if observed_pending else "processed"
    fingerprint = sha256(json.dumps({"type": event_type, "object_id": obj.get("id")}, sort_keys=True).encode()).hexdigest()
    action = {
        "checkout.session.async_payment_failed": "payment_failed",
        "checkout.session.expired": "expired",
        "charge.refunded": "refund_detected",
        "refund.created": "refund_detected",
        "refund.updated": "refund_detected",
        "charge.dispute.created": "dispute_pending",
        "charge.dispute.updated": "dispute_pending",
        "charge.dispute.closed": "dispute_won" if obj.get("status") == "won" else "dispute_lost",
    }.get(event_type, "observed")
    if session.bind.dialect.name == "postgresql":
        session.execute(text("""
            select public.record_stripe_event_observation(
                :id, :event_type, :fingerprint, :object_id, :session_id,
                :payment_intent_id, :livemode, :action
            )
        """), {
            "id": event["id"], "event_type": event_type, "fingerprint": fingerprint,
            "object_id": obj.get("id"), "session_id": order.stripe_checkout_session_id if order else session_id,
            "payment_intent_id": order.stripe_payment_intent_id if order else obj.get("payment_intent"),
            "livemode": event_livemode, "action": action,
        })
        session.commit()
        return
    recorded = session.get(StripeEvent, event["id"])
    if recorded:
        if recorded.facts_fingerprint != fingerprint:
            raise DeterministicPaymentConflict("event_facts_conflict")
        return
    recorded = StripeEvent(
        stripe_event_id=event["id"], event_type=event_type, facts_fingerprint=fingerprint,
        purchase_id=order.id if order else None, status=status, attempt_count=1,
        livemode=bool(event.get("livemode")), stripe_object_id=obj.get("id"),
        processed_at=datetime.now(timezone.utc) if status == "processed" else None,
    )
    session.add(recorded)
    if order and order.status == "pending":
        if event_type == "checkout.session.async_payment_failed":
            order.status = "failed"
        elif event_type == "checkout.session.expired":
            order.status = "cancelled"
    if order and event_type == "charge.refunded":
        order.refund_detected_at = datetime.now(timezone.utc)
    if order and event_type in {"charge.dispute.created", "charge.dispute.updated"}:
        order.dispute_status = "needs_review"
    if order and event_type == "charge.dispute.closed":
        order.dispute_status = "won" if obj.get("status") == "won" else "lost"
    session.commit()


def record_webhook_conflict(session: Session, event: dict, conflict: DeterministicPaymentConflict) -> None:
    obj = event.get("data", {}).get("object", {})
    record_stripe_event_failure(
        session,
        stripe_event_id=event["id"], stripe_event_type=event.get("type", "unknown"),
        facts={"object_id": obj.get("id"), "event_type": event.get("type")},
        reason_code=conflict.reason_code, error=str(conflict), livemode=bool(event.get("livemode")),
    )
