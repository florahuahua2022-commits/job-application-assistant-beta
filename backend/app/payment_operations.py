from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel import Session, select

from .models import PackCreditLedger, PaymentOperationAudit, Purchase, StripeEvent
from .pack_credits import process_stripe_purchase_event


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stripe_event_id: str | None = None
    checkout_session_id: str | None = None

    @model_validator(mode="after")
    def exactly_one_target(self):
        if bool(self.stripe_event_id) == bool(self.checkout_session_id):
            raise ValueError("Provide exactly one replay target")
        return self


def failed_events(session: Session) -> list[dict]:
    rows = session.exec(select(StripeEvent).where(
        StripeEvent.status.in_(["failed", "observed_pending"])
    ).order_by(StripeEvent.created_at.desc())).all()
    return [{
        "stripe_event_id": row.stripe_event_id,
        "event_type": row.event_type,
        "status": row.status,
        "failure_reason_code": row.failure_reason_code,
        "stripe_object_id": row.stripe_object_id,
        "purchase_id": row.purchase_id,
        "attempt_count": row.attempt_count,
        "created_at": row.created_at,
    } for row in rows]


def reconciliation(session: Session) -> dict:
    orders = session.exec(select(Purchase)).all()
    events = session.exec(select(StripeEvent)).all()
    grants = session.exec(select(PackCreditLedger).where(
        PackCreditLedger.entry_type == "grant_stripe_purchase"
    )).all()
    granted_purchase_ids = {row.purchase_id for row in grants}
    return {
        "paid_without_grant": [row.id for row in orders if row.status == "paid" and row.id not in granted_purchase_ids],
        "failed_or_observed_event_ids": [row.stripe_event_id for row in events if row.status in {"failed", "observed_pending"}],
        "expired_pending_order_ids": [row.id for row in orders if row.status == "pending" and row.expires_at],
        "refund_or_dispute_order_ids": [
            row.id for row in orders if row.refund_detected_at or row.dispute_status != "none"
        ],
    }


def replay(session: Session, gateway, admin_user_id, request: ReplayRequest) -> dict:
    if request.stripe_event_id:
        remote_event = gateway.retrieve_event(request.stripe_event_id)
        event = remote_event if isinstance(remote_event, dict) else dict(remote_event)
        obj = event["data"]["object"]
        session_id = obj["id"]
        event_id, event_type = event["id"], event["type"]
    else:
        remote = gateway.retrieve_checkout_session(request.checkout_session_id)
        obj = remote if isinstance(remote, dict) else vars(remote)
        session_id = request.checkout_session_id
        event_id, event_type = f"admin_replay:{session_id}", "admin.replay"
    order = session.exec(select(Purchase).where(Purchase.stripe_checkout_session_id == session_id)).one()
    before = order.status
    balance = process_stripe_purchase_event(
        session, stripe_event_id=event_id, stripe_event_type=event_type, livemode=bool(obj.get("livemode")),
        stripe_checkout_session_id=session_id, stripe_payment_intent_id=obj["payment_intent"],
        user_id=order.user_id, package_code=order.package_code, credits=order.credits,
        subtotal_cents=order.subtotal_cents, gst_cents=order.gst_cents,
        total_paid_cents=order.total_paid_cents, single_pack_price_cents=order.single_pack_price_cents,
        currency=order.currency, gst_enabled=order.gst_enabled, catalog_version=order.catalog_version,
    )
    session.add(PaymentOperationAudit(
        admin_user_id=admin_user_id, operation="replay", target_type="event" if request.stripe_event_id else "session",
        target_id=request.stripe_event_id or session_id, before_status=before, after_status="paid", result="processed",
    ))
    session.commit()
    return {"result": "processed", "balance": balance, "event_id": event_id}
