"""Atomic Pack credit operations. Payment collection is intentionally out of scope."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from pydantic import BaseModel
from sqlmodel import Session, select

from .models import GenerationUsage, GlobalMonthlyUsage, PackCreditAccount, PackCreditLedger

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
    package_code: Literal["single", "starter", "job_search"]
    idempotency_key: str
    note: str


class ManualTopupResponse(BaseModel):
    user_id: UUID
    package_code: str
    balance: int


def _month_start(now: datetime) -> date:
    return now.astimezone(timezone.utc).date().replace(day=1)


def _ensure_account(session: Session, user_id: UUID) -> None:
    statement = sqlite_insert(PackCreditAccount).values(user_id=user_id, balance=2).on_conflict_do_nothing()
    created = session.execute(statement).rowcount == 1
    if created:
        session.execute(sqlite_insert(PackCreditLedger).values(
            user_id=user_id, entry_type="grant_free", credits_delta=2,
            idempotency_key=f"grant-free:{user_id}", note="Initial lifetime credits",
        ).on_conflict_do_nothing())


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
    admin_user_id: UUID, note: str,
) -> int:
    package = PACK_CATALOG.get(package_code)
    if not package:
        raise ValueError("Unknown package code.")
    if session.bind.dialect.name == "postgresql":
        return int(session.execute(text(
            "SELECT public.grant_manual_pack_topup(:user_id, :package_code, :credits, :amount, :key, :admin_id, :note)"
        ), {
            "user_id": user_id, "package_code": package_code, "credits": package["credits"],
            "amount": package["amount_cents"], "key": idempotency_key,
            "admin_id": admin_user_id, "note": note,
        }).scalar_one())
    _ensure_account(session, user_id)
    existing = session.exec(select(PackCreditLedger).where(
        PackCreditLedger.idempotency_key == idempotency_key,
    )).first()
    if existing:
        if (
            existing.user_id != user_id or existing.entry_type != "grant_manual_topup"
            or existing.package_code != package_code or existing.credits_delta != package["credits"]
            or existing.amount_cents != package["amount_cents"]
        ):
            raise ValueError("Idempotency key is already used for a different top-up.")
        return session.get(PackCreditAccount, user_id).balance
    inserted = session.execute(sqlite_insert(PackCreditLedger).values(
        user_id=user_id, entry_type="grant_manual_topup", credits_delta=package["credits"],
        package_code=package_code, amount_cents=package["amount_cents"], currency=package["currency"],
        note=note, idempotency_key=idempotency_key, created_by_user_id=admin_user_id,
    ).on_conflict_do_nothing()).rowcount
    if not inserted:
        raise ValueError("Idempotency key is already used for a different top-up.")
    session.execute(update(PackCreditAccount).where(PackCreditAccount.user_id == user_id).values(
        balance=PackCreditAccount.balance + package["credits"], updated_at=datetime.now(timezone.utc),
    ))
    session.flush()
    return session.get(PackCreditAccount, user_id).balance
