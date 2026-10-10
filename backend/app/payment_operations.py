from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import text
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


def backend_privilege_check(session: Session) -> dict:
    tables = (
        "globalmonthlyusage", "packcreditaccount", "packcreditledger", "generationusage",
        "purchase", "stripeevent", "packcreditlot", "packcreditallocation", "paymentrefund",
        "paymentcheckoutrate", "paymentoperationaudit",
    )
    sequences = (
        "packcreditledger_id_seq", "generationusage_id_seq", "purchase_id_seq",
        "packcreditlot_id_seq", "packcreditallocation_id_seq", "paymentrefund_id_seq",
        "paymentcheckoutrate_id_seq", "paymentoperationaudit_id_seq",
    )
    functions = {
        "process_stripe_purchase_event": "public.process_stripe_purchase_event(text,text,text,text,text,uuid,text,integer,integer,integer,integer,integer,text,boolean,boolean,text)",
        "record_stripe_event_failure": "public.record_stripe_event_failure(text,text,text,text,text,boolean)",
        "record_stripe_event_observation": "public.record_stripe_event_observation(text,text,text,text,text,text,boolean,text)",
        "reserve_checkout_creation": "public.reserve_checkout_creation(uuid,text,text)",
        "get_available_pack_credits": "public.get_available_pack_credits(uuid)",
        "reserve_pack_credits": "public.reserve_pack_credits(uuid,uuid,integer,integer)",
        "complete_pack_credits": "public.complete_pack_credits(uuid,uuid)",
        "release_pack_credits": "public.release_pack_credits(uuid,uuid)",
    }
    checks = []
    for name in tables:
        allowed = session.execute(text("select has_table_privilege(current_user,:object,'select,insert,update,delete')"),
                                  {"object": f"public.{name}"}).scalar_one()
        checks.append({"kind": "table", "name": name, "allowed": allowed})
    for name in sequences:
        allowed = session.execute(text("select has_sequence_privilege(current_user,:object,'usage,select')"),
                                  {"object": f"public.{name}"}).scalar_one()
        checks.append({"kind": "sequence", "name": name, "allowed": allowed})
    for name, signature in functions.items():
        allowed = session.execute(text("select has_function_privilege(current_user,:object,'execute')"),
                                  {"object": signature}).scalar_one()
        checks.append({"kind": "function", "name": name, "allowed": allowed})
    role = session.execute(text("select current_user")).scalar_one()
    return {"ready": all(item["allowed"] for item in checks), "database_role": role, "checks": checks}


def failed_events(
    session: Session, *, status: str | None = None, event_type: str | None = None,
    limit: int = 50, offset: int = 0,
) -> dict:
    query = select(StripeEvent).where(StripeEvent.status.in_(["failed", "observed_pending"]))
    if status:
        query = query.where(StripeEvent.status == status)
    if event_type:
        query = query.where(StripeEvent.event_type == event_type)
    rows = session.exec(query.order_by(StripeEvent.created_at.desc()).offset(offset).limit(limit + 1)).all()
    items = [{
        "stripe_event_id": row.stripe_event_id,
        "event_type": row.event_type,
        "status": row.status,
        "failure_reason_code": row.failure_reason_code,
        "stripe_object_id": row.stripe_object_id,
        "purchase_id": row.purchase_id,
        "attempt_count": row.attempt_count,
        "created_at": row.created_at,
    } for row in rows[:limit]]
    return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}


def reconciliation(session: Session, gateway=None) -> dict:
    orders = session.exec(select(Purchase)).all()
    events = session.exec(select(StripeEvent)).all()
    grants = session.exec(select(PackCreditLedger).where(
        PackCreditLedger.entry_type == "grant_stripe_purchase"
    )).all()
    granted_purchase_ids = {row.purchase_id for row in grants if row.purchase_id is not None}
    processed_purchase_ids = {row.purchase_id for row in events if row.status == "processed" and row.purchase_id is not None}
    now = datetime.now(timezone.utc)
    stripe_status_mismatches, stripe_status_errors = [], []
    if gateway:
        for order in orders:
            try:
                remote = gateway.retrieve_checkout_session(order.stripe_checkout_session_id)
                obj = remote if isinstance(remote, dict) else vars(remote)
                remote_paid = obj.get("payment_status") == "paid"
                if remote_paid != (order.status == "paid") or bool(obj.get("livemode")) != order.livemode:
                    stripe_status_mismatches.append(order.id)
            except Exception:
                stripe_status_errors.append(order.id)
    return {
        "paid_without_grant": [row.id for row in orders if row.status == "paid" and row.id not in granted_purchase_ids],
        "failed_or_observed_event_ids": [row.stripe_event_id for row in events if row.status in {"failed", "observed_pending"}],
        "processed_event_without_order_ids": [row.stripe_event_id for row in events if row.status == "processed" and row.purchase_id is None],
        "grant_without_processed_event_purchase_ids": sorted(granted_purchase_ids - processed_purchase_ids),
        "mode_mismatch_event_ids": [
            row.stripe_event_id for row in events for order in orders
            if row.purchase_id == order.id and row.livemode != order.livemode
        ],
        "expired_pending_order_ids": [
            row.id for row in orders if row.status == "pending" and row.expires_at
            and (row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)) <= now
        ],
        "refund_or_dispute_order_ids": [
            row.id for row in orders if row.refund_detected_at or row.dispute_status != "none"
        ],
        "stripe_status_mismatch_order_ids": stripe_status_mismatches,
        "stripe_status_check_error_order_ids": stripe_status_errors,
    }


def replay(session: Session, gateway, admin_user_id, request: ReplayRequest) -> dict:
    if request.stripe_event_id:
        try:
            remote_event = gateway.retrieve_event(request.stripe_event_id)
        except Exception as error:
            raise ValueError("Stripe Event is unavailable or outside Stripe's retention window") from error
        event = remote_event if isinstance(remote_event, dict) else dict(remote_event)
        if event.get("type") not in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            raise ValueError("Only successful Checkout events can be replayed")
        obj = event["data"]["object"]
        session_id = obj["id"]
        event_id, event_type = event["id"], event["type"]
        event_livemode = bool(event.get("livemode"))
    else:
        remote = gateway.retrieve_checkout_session(request.checkout_session_id)
        obj = remote if isinstance(remote, dict) else vars(remote)
        session_id = request.checkout_session_id
        event_id, event_type = f"admin_replay:{session_id}", "admin.replay"
        event_livemode = bool(obj.get("livemode"))
    order = session.exec(select(Purchase).where(Purchase.stripe_checkout_session_id == session_id)).one()
    metadata = obj.get("metadata", {})
    if (
        obj.get("payment_status") != "paid" or obj.get("mode") != "payment"
        or obj.get("payment_intent") is None
        or str(obj.get("currency", "")).upper() != order.currency
        or obj.get("amount_total") != order.total_paid_cents
        or event_livemode != order.livemode
        or metadata.get("user_id") != str(order.user_id)
        or metadata.get("package_code") != order.package_code
        or metadata.get("credits") != str(order.credits)
        or metadata.get("gst_enabled") != str(order.gst_enabled).lower()
        or metadata.get("catalog_version") != order.catalog_version
    ):
        raise ValueError("Replay facts do not match the stored order")
    before = order.status
    balance = process_stripe_purchase_event(
        session, stripe_event_id=event_id, stripe_event_type=event_type, livemode=event_livemode,
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
