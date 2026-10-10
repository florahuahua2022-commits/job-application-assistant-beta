"""Atomic Pack credit operations. Payment collection is intentionally out of scope."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from pydantic import BaseModel
from sqlmodel import Session, select

from .models import GenerationUsage, GlobalMonthlyUsage, PackCreditAccount, PackCreditLedger, Purchase, StripeEvent

PACK_CATALOG = {
    "single": {"credits": 1, "amount_cents": 1695, "currency": "AUD"},
    "starter": {"credits": 8, "amount_cents": 10995, "currency": "AUD"},
    "job_search": {"credits": 18, "amount_cents": 19900, "currency": "AUD"},
}

RESERVATION_MINUTES = 30


@dataclass(frozen=True)
class CreditReservation:
    status: str
    balance: int


class ManualTopupRequest(BaseModel):
    user_id: UUID
    package_code: Literal["single", "starter", "job_search", "custom"]
    credits: int | None = None
    idempotency_key: str
    note: str


class ManualTopupResponse(BaseModel):
    user_id: UUID
    package_code: str
    balance: int


class PackCreditAccessResponse(BaseModel):
    unlimited: bool = False
    balance: int | None = None
    standard_pack_cost: int = 1
    selection_criteria_pack_cost: int = 2


def _month_start(now: datetime) -> date:
    return now.astimezone(timezone.utc).date().replace(day=1)


def _ensure_account(session: Session, user_id: UUID) -> None:
    insert = postgresql_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    statement = insert(PackCreditAccount).values(user_id=user_id, balance=2).on_conflict_do_nothing()
    created = session.execute(statement).rowcount == 1
    if created:
        session.execute(insert(PackCreditLedger).values(
            user_id=user_id, entry_type="grant_free", credits_delta=2,
            idempotency_key=f"grant-free:{user_id}", note="Initial lifetime credits",
        ).on_conflict_do_nothing())


def pack_credit_balance(session: Session, user_id: UUID) -> int:
    if session.bind.dialect.name == "postgresql":
        balance = session.execute(text(
            "select public.get_available_pack_credits(:user_id)"
        ), {"user_id": user_id}).scalar_one_or_none()
        return 0 if balance is None else int(balance)
    _ensure_account(session, user_id)
    session.flush()
    return session.get(PackCreditAccount, user_id).balance


def reserve_pack_credits(
    session: Session, user_id: UUID, pack_id: UUID, credit_cost: int, monthly_limit: int = 500,
) -> CreditReservation:
    if credit_cost not in {1, 2}:
        raise ValueError("Pack credit cost must be 1 or 2.")
    if session.bind.dialect.name == "postgresql":
        row = session.execute(text(
            "SELECT result_status, remaining_balance FROM public.reserve_pack_credits(:user_id, :pack_id, :cost, :limit)"
        ), {"user_id": user_id, "pack_id": pack_id, "cost": credit_cost, "limit": monthly_limit}).one()
        return CreditReservation(row.result_status, row.remaining_balance)

    _ensure_account(session, user_id)
    account = session.get(PackCreditAccount, user_id)
    if session.exec(select(GenerationUsage.id).where(
        GenerationUsage.user_id == user_id, GenerationUsage.pack_id == pack_id,
    )).first():
        return CreditReservation("existing", account.balance)
    debit = session.execute(sqlite_insert(PackCreditLedger).values(
        user_id=user_id, entry_type="debit_generation", credits_delta=-credit_cost,
        pack_id=pack_id, idempotency_key=f"generation:{user_id}:{pack_id}",
    ).on_conflict_do_nothing()).rowcount
    if not debit:
        return CreditReservation("existing", account.balance)

    now = datetime.now(timezone.utc)
    month = _month_start(now)
    session.execute(sqlite_insert(GlobalMonthlyUsage).values(month_start=month).on_conflict_do_nothing())
    counter_updated = session.execute(
        update(GlobalMonthlyUsage).where(
            GlobalMonthlyUsage.month_start == month,
            GlobalMonthlyUsage.reserved_count + GlobalMonthlyUsage.completed_count < monthly_limit,
        ).values(reserved_count=GlobalMonthlyUsage.reserved_count + 1, updated_at=now)
    ).rowcount
    if not counter_updated:
        session.execute(delete(PackCreditLedger).where(PackCreditLedger.idempotency_key == f"generation:{user_id}:{pack_id}"))
        return CreditReservation("global_limit", account.balance)

    account_updated = session.execute(
        update(PackCreditAccount).where(
            PackCreditAccount.user_id == user_id, PackCreditAccount.balance >= credit_cost,
        ).values(balance=PackCreditAccount.balance - credit_cost, updated_at=now)
    ).rowcount
    if not account_updated:
        session.execute(update(GlobalMonthlyUsage).where(
            GlobalMonthlyUsage.month_start == month,
        ).values(reserved_count=GlobalMonthlyUsage.reserved_count - 1, updated_at=now))
        session.execute(delete(PackCreditLedger).where(PackCreditLedger.idempotency_key == f"generation:{user_id}:{pack_id}"))
        return CreditReservation("insufficient_credits", account.balance)

    session.add(GenerationUsage(
        user_id=user_id, pack_id=pack_id, status="reserved", credit_cost=credit_cost,
        generated_at=now, reserved_at=now, expires_at=now + timedelta(minutes=RESERVATION_MINUTES),
        usage_month=month,
    ))
    session.flush()
    return CreditReservation("reserved", account.balance)


def complete_pack_credits(session: Session, user_id: UUID, pack_id: UUID) -> bool:
    if session.bind.dialect.name == "postgresql":
        return bool(session.execute(text(
            "SELECT public.complete_pack_credits(:user_id, :pack_id)"
        ), {"user_id": user_id, "pack_id": pack_id}).scalar_one())
    now = datetime.now(timezone.utc)
    usage = session.execute(
        update(GenerationUsage).where(
            GenerationUsage.user_id == user_id, GenerationUsage.pack_id == pack_id,
            GenerationUsage.status == "reserved",
        ).values(status="completed", completed_at=now).returning(GenerationUsage.usage_month)
    ).first()
    if not usage:
        return False
    session.execute(update(GlobalMonthlyUsage).where(
        GlobalMonthlyUsage.month_start == usage[0], GlobalMonthlyUsage.reserved_count > 0,
    ).values(
        reserved_count=GlobalMonthlyUsage.reserved_count - 1,
        completed_count=GlobalMonthlyUsage.completed_count + 1,
        updated_at=now,
    ))
    return True


def release_pack_credits(session: Session, user_id: UUID, pack_id: UUID) -> bool:
    if session.bind.dialect.name == "postgresql":
        return bool(session.execute(text(
            "SELECT public.release_pack_credits(:user_id, :pack_id)"
        ), {"user_id": user_id, "pack_id": pack_id}).scalar_one())
    now = datetime.now(timezone.utc)
    usage = session.execute(
        update(GenerationUsage).where(
            GenerationUsage.user_id == user_id, GenerationUsage.pack_id == pack_id,
            GenerationUsage.status == "reserved",
        ).values(status="released", released_at=now).returning(
            GenerationUsage.credit_cost, GenerationUsage.usage_month,
        )
    ).first()
    if not usage:
        return False
    cost, month = usage
    session.execute(update(PackCreditAccount).where(PackCreditAccount.user_id == user_id).values(
        balance=PackCreditAccount.balance + cost, updated_at=now,
    ))
    session.execute(update(GlobalMonthlyUsage).where(
        GlobalMonthlyUsage.month_start == month, GlobalMonthlyUsage.reserved_count > 0,
    ).values(reserved_count=GlobalMonthlyUsage.reserved_count - 1, updated_at=now))
    session.execute(sqlite_insert(PackCreditLedger).values(
        user_id=user_id, entry_type="release", credits_delta=cost, pack_id=pack_id,
        idempotency_key=f"release:{user_id}:{pack_id}",
    ).on_conflict_do_nothing())
    return True


def expire_pack_reservations(session: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    expired = session.exec(select(GenerationUsage).where(
        GenerationUsage.status == "reserved", GenerationUsage.expires_at <= now,
    )).all()
    return sum(release_pack_credits(session, usage.user_id, usage.pack_id) for usage in expired)


def grant_manual_topup(
    session: Session, user_id: UUID, package_code: str, idempotency_key: str,
    admin_user_id: UUID, note: str, credits: int | None = None,
) -> int:
    if package_code == "custom":
        if type(credits) is not int or credits <= 0:
            raise ValueError("Custom credits must be a positive integer.")
        package = {"credits": credits, "amount_cents": 0, "currency": "AUD"}
    else:
        package = PACK_CATALOG.get(package_code)
        if not package:
            raise ValueError("Unknown package code.")
        if credits is not None:
            raise ValueError("Credits are only accepted for a custom top-up.")
    if session.bind.dialect.name == "postgresql":
        return int(session.execute(text(
            "SELECT public.grant_manual_pack_topup(:user_id, :package_code, :credits, :amount, :key, :admin_id, :note)"
        ), {
            "user_id": user_id, "package_code": package_code, "credits": package["credits"],
            "amount": package["amount_cents"], "key": idempotency_key,
            "admin_id": admin_user_id, "note": note,
        }).scalar_one())
    return _grant_pack_credits(
        session, user_id, "manual", package_code, package["credits"], package["amount_cents"],
        idempotency_key, admin_user_id=admin_user_id, note=note,
    )


def _grant_pack_credits(
    session: Session, user_id: UUID, source: str, package_code: str, credits: int,
    amount_cents: int, idempotency_key: str, purchase_id: int | None = None,
    admin_user_id: UUID | None = None, note: str | None = None,
) -> int:
    if source not in {"manual", "stripe"}:
        raise ValueError("Unknown credit source.")
    if source == "manual" and (admin_user_id is None or purchase_id is not None):
        raise ValueError("Manual grants require an administrator and no purchase.")
    if source == "stripe" and (purchase_id is None or admin_user_id is not None):
        raise ValueError("Stripe grants require a purchase and no administrator.")
    _ensure_account(session, user_id)
    existing = session.exec(select(PackCreditLedger).where(
        PackCreditLedger.idempotency_key == idempotency_key,
    )).first()
    entry_type = "grant_manual_topup" if source == "manual" else "grant_stripe_purchase"
    if existing:
        if (
            existing.user_id != user_id or existing.entry_type != entry_type
            or existing.package_code != package_code or existing.credits_delta != credits
            or existing.amount_cents != amount_cents or existing.purchase_id != purchase_id
        ):
            raise ValueError("Idempotency key is already used for a different top-up.")
        return session.get(PackCreditAccount, user_id).balance
    inserted = session.execute(sqlite_insert(PackCreditLedger).values(
        user_id=user_id, entry_type=entry_type, credits_delta=credits,
        package_code=package_code, amount_cents=amount_cents, currency="AUD",
        note=note, idempotency_key=idempotency_key, created_by_user_id=admin_user_id,
        purchase_id=purchase_id,
    ).on_conflict_do_nothing()).rowcount
    if not inserted:
        raise ValueError("Idempotency key is already used for a different top-up.")
    session.execute(update(PackCreditAccount).where(PackCreditAccount.user_id == user_id).values(
        balance=PackCreditAccount.balance + credits, updated_at=datetime.now(timezone.utc),
    ))
    session.flush()
    return session.get(PackCreditAccount, user_id).balance


def _event_fingerprint(facts: dict) -> str:
    return sha256(json.dumps(facts, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _payment_failure_reason(error: Exception) -> str:
    sqlstate = getattr(getattr(error, "orig", error), "sqlstate", None)
    if sqlstate in {"55P03", "57014", "40P01", "40001"}:
        return "lock_timeout"
    if sqlstate and sqlstate.startswith("08"):
        return "database_unavailable"
    return "internal_error"


def record_stripe_event_failure(
    session: Session, *, stripe_event_id: str, stripe_event_type: str,
    facts: dict, reason_code: str, error: str, livemode: bool,
) -> None:
    fingerprint = _event_fingerprint(facts)
    if session.bind.dialect.name == "postgresql":
        session.execute(text("""
            select public.record_stripe_event_failure(
                :id, :event_type, :fingerprint, :reason_code, :error, :livemode
            )
        """), {
            "id": stripe_event_id, "event_type": stripe_event_type,
            "fingerprint": fingerprint, "reason_code": reason_code,
            "error": error[:1000], "livemode": livemode,
        })
        session.commit()
        return
    event = session.get(StripeEvent, stripe_event_id)
    if event and event.facts_fingerprint != fingerprint:
        raise ValueError("Stripe event facts do not match the original delivery.")
    if not event:
        event = StripeEvent(
            stripe_event_id=stripe_event_id, event_type=stripe_event_type,
            facts_fingerprint=fingerprint, status="failed", attempt_count=1,
            livemode=livemode,
        )
        session.add(event)
    else:
        event.status, event.attempt_count = "failed", event.attempt_count + 1
    event.failure_reason_code = reason_code
    event.last_error = error[:1000]
    event.updated_at = datetime.now(timezone.utc)
    session.commit()


def process_stripe_purchase_event(
    session: Session, *, stripe_event_id: str, stripe_checkout_session_id: str,
    stripe_payment_intent_id: str, user_id: UUID, package_code: str, credits: int,
    subtotal_cents: int, gst_cents: int, total_paid_cents: int,
    single_pack_price_cents: int, currency: str,
    stripe_event_type: str = "checkout.session.completed", livemode: bool = False,
    failure_reason_code: str | None = None,
    gst_enabled: bool = False, catalog_version: str = "2026-10-09",
) -> int:
    facts = {
        "stripe_checkout_session_id": stripe_checkout_session_id,
        "stripe_payment_intent_id": stripe_payment_intent_id,
        "user_id": str(user_id), "package_code": package_code, "credits": credits,
        "subtotal_cents": subtotal_cents, "gst_cents": gst_cents,
        "total_paid_cents": total_paid_cents,
        "single_pack_price_cents": single_pack_price_cents, "currency": currency,
        "stripe_event_type": stripe_event_type, "livemode": livemode,
        "gst_enabled": gst_enabled, "catalog_version": catalog_version,
    }
    fingerprint = _event_fingerprint(facts)
    try:
        if session.bind.dialect.name == "postgresql":
            balance = session.execute(text("""
                select public.process_stripe_purchase_event(
                    :event_id, :event_type, :fingerprint, :session_id, :payment_intent_id,
                    :user_id, :package_code, :credits, :subtotal, :gst, :total,
                    :single_price, :currency, :livemode, :gst_enabled, :catalog_version
                )
            """), {"event_id": stripe_event_id, "event_type": stripe_event_type,
                  "fingerprint": fingerprint,
                  "session_id": stripe_checkout_session_id,
                  "payment_intent_id": stripe_payment_intent_id, "user_id": user_id,
                  "package_code": package_code, "credits": credits, "subtotal": subtotal_cents,
                  "gst": gst_cents, "total": total_paid_cents, "single_price": single_pack_price_cents,
                  "currency": currency, "livemode": livemode, "gst_enabled": gst_enabled,
                  "catalog_version": catalog_version}).scalar_one()
        else:
            event = session.get(StripeEvent, stripe_event_id)
            if event and event.facts_fingerprint != fingerprint:
                raise ValueError("Stripe event facts do not match the original delivery.")
            if event and event.status == "processed":
                return session.get(PackCreditAccount, user_id).balance
            if not event:
                event = StripeEvent(
                    stripe_event_id=stripe_event_id, event_type=stripe_event_type,
                    facts_fingerprint=fingerprint, status="processing", attempt_count=1,
                    livemode=livemode,
                )
                session.add(event)
            else:
                event.status, event.attempt_count, event.last_error = "processing", event.attempt_count + 1, None
            purchase = session.exec(select(Purchase).where(
                Purchase.stripe_checkout_session_id == stripe_checkout_session_id
            )).first()
            if not purchase:
                purchase = Purchase(
                    user_id=user_id, stripe_checkout_session_id=stripe_checkout_session_id,
                    stripe_payment_intent_id=stripe_payment_intent_id, status="pending",
                    package_code=package_code, credits=credits, amount_cents=total_paid_cents,
                    subtotal_cents=subtotal_cents, gst_cents=gst_cents,
                    total_paid_cents=total_paid_cents, single_pack_price_cents=single_pack_price_cents,
                    currency=currency, gst_enabled=gst_enabled, livemode=livemode,
                    catalog_version=catalog_version,
                )
                session.add(purchase); session.flush()
            elif (
                purchase.status not in {"pending", "paid"}
                or purchase.user_id != user_id
                or (purchase.stripe_payment_intent_id is not None
                    and purchase.stripe_payment_intent_id != stripe_payment_intent_id)
                or purchase.package_code != package_code or purchase.credits != credits
                or purchase.subtotal_cents != subtotal_cents or purchase.gst_cents != gst_cents
                or purchase.total_paid_cents != total_paid_cents
                or purchase.single_pack_price_cents != single_pack_price_cents
                or purchase.currency != currency or purchase.livemode != livemode
                or purchase.gst_enabled != gst_enabled or purchase.catalog_version != catalog_version
            ):
                raise ValueError("Checkout session facts conflict")
            if purchase.status == "paid":
                event.purchase_id, event.status = purchase.id, "processed"
                event.processed_at = event.updated_at = datetime.now(timezone.utc)
                session.commit()
                return session.get(PackCreditAccount, user_id).balance
            if purchase.stripe_payment_intent_id is None:
                purchase.stripe_payment_intent_id = stripe_payment_intent_id
            balance = _grant_pack_credits(
                session, user_id, "stripe", package_code, credits, total_paid_cents,
                f"stripe-purchase:{stripe_checkout_session_id}", purchase_id=purchase.id,
            )
            now = datetime.now(timezone.utc)
            purchase.status, purchase.paid_at, purchase.updated_at = "paid", now, now
            event.purchase_id, event.status, event.processed_at, event.updated_at = purchase.id, "processed", now, now
        session.commit()
        return int(balance)
    except Exception as error:
        session.rollback()
        if session.bind.dialect.name == "postgresql":
            session.execute(text("""
                select public.record_stripe_event_failure(
                    :id, :event_type, :fingerprint, :reason_code, :error, :livemode
                )
            """), {
                "id": stripe_event_id, "event_type": stripe_event_type,
                "fingerprint": fingerprint, "reason_code": failure_reason_code or _payment_failure_reason(error),
                "error": str(error)[:1000], "livemode": livemode,
            })
        else:
            event = session.get(StripeEvent, stripe_event_id)
            if event and event.facts_fingerprint != fingerprint:
                raise
            if not event:
                event = StripeEvent(
                    stripe_event_id=stripe_event_id, event_type=stripe_event_type,
                    facts_fingerprint=fingerprint, status="failed", attempt_count=1,
                    livemode=livemode, failure_reason_code=failure_reason_code or _payment_failure_reason(error),
                )
                session.add(event)
            else:
                event.status, event.attempt_count = "failed", event.attempt_count + 1
            event.last_error = str(error)[:1000]
            event.failure_reason_code = failure_reason_code or _payment_failure_reason(error)
            event.updated_at = datetime.now(timezone.utc)
        session.commit()
        raise
