from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, func, select

from .auth import AuthenticatedUser
from .config import settings
from .models import PaymentCheckoutRate, Purchase


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
    key = configured.stripe_secret_key
    if not key:
        return
    if not configured.stripe_webhook_secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET is required when Stripe is enabled")
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
        expires_at=expires_at,
        checkout_idempotency_key_hash=key_hash,
    ))
    session.commit()
    return CheckoutSessionResponse(checkout_session_id=remote.id, checkout_url=remote.url)
