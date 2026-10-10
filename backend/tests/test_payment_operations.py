import unittest
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import PaymentOperationAudit, Purchase, StripeEvent
from app.payment_operations import ReplayRequest, failed_events, reconciliation, replay


class PaymentOperationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.user, self.admin = uuid4(), uuid4()

    def tearDown(self):
        self.engine.dispose()

    def test_failed_list_and_reconciliation_are_read_only(self):
        with Session(self.engine) as session:
            session.add(StripeEvent(
                stripe_event_id="evt_failed", event_type="checkout.session.completed",
                facts_fingerprint="facts", status="failed", attempt_count=1,
                failure_reason_code="amount_mismatch",
            ))
            session.commit()
            before = session.exec(select(PaymentOperationAudit)).all()
            listed = failed_events(session)
            report = reconciliation(session)
            after = session.exec(select(PaymentOperationAudit)).all()
        self.assertEqual(listed["items"][0]["failure_reason_code"], "amount_mismatch")
        self.assertEqual(report["failed_or_observed_event_ids"], ["evt_failed"])
        self.assertEqual((before, after), ([], []))

    def test_session_replay_uses_admin_identity_and_is_idempotent(self):
        with Session(self.engine) as session:
            session.add(Purchase(
                user_id=self.user, stripe_checkout_session_id="cs_replay", status="pending",
                package_code="single", currency="AUD", credits=1, amount_cents=1695,
                subtotal_cents=1695, gst_cents=0, total_paid_cents=1695,
                single_pack_price_cents=1695,
            ))
            session.commit()
            gateway = SimpleNamespace(retrieve_checkout_session=lambda _id: {
                "id": "cs_replay", "payment_intent": "pi_replay", "livemode": False,
                "payment_status": "paid", "mode": "payment", "currency": "aud", "amount_total": 1695,
                "metadata": {"user_id": str(self.user), "package_code": "single", "credits": "1",
                             "gst_enabled": "false", "catalog_version": "2026-10-09"},
            })
            first = replay(session, gateway, self.admin, ReplayRequest(checkout_session_id="cs_replay"))
            second = replay(session, gateway, self.admin, ReplayRequest(checkout_session_id="cs_replay"))
            event = session.get(StripeEvent, "admin_replay:cs_replay")
            audits = session.exec(select(PaymentOperationAudit)).all()
        self.assertEqual((first["balance"], second["balance"]), (3, 3))
        self.assertEqual(event.event_type, "admin.replay")
        self.assertEqual(len(audits), 2)


if __name__ == "__main__":
    unittest.main()
