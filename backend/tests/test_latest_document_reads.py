import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.sql import visitors
from sqlalchemy.sql.selectable import Select
from sqlmodel import Session, SQLModel, delete, select

from app import main
from app.application_requirements import empty_application_requirements
from app.models import ApplicantProfile, GeneratedDocument, GenerateRequest, JobApplication, QualityCheckResponse, Resume
from app.release_state import generation_inputs_fingerprint
from egress_probe import measured_engine


class LatestDocumentReadTests(unittest.TestCase):
    engine_factory = staticmethod(measured_engine)

    def setUp(self):
        self.records, self.statements = [], []
        self.engine = self.engine_factory(self.records)
        SQLModel.metadata.create_all(self.engine)
        self.owner, self.pack = uuid4(), uuid4()
        requirements = empty_application_requirements("Coordinate projects.", source="user_supplied")
        requirements["review_status"] = "confirmed"
        for kind, document in requirements["documents"].items():
            document.update(requirement="required" if kind in {"resume", "cover_letter"} else "not_required", format="standalone" if kind in {"resume", "cover_letter"} else "not_applicable")
        with Session(self.engine) as session:
            application = JobApplication(user_id=self.owner, company="Current Department", position_title="Officer", job_description="Coordinate projects.", application_requirements_json=json.dumps(requirements))
            session.add(application)
            session.commit()
            self.application_id = application.id
        event.listen(self.engine, "before_execute", self.capture)
        self.seed(30)

    def capture(self, connection, clause, multiparams, params, options):
        if isinstance(clause, Select):
            self.statements.append(clause)

    def tearDown(self):
        event.remove(self.engine, "before_execute", self.capture)
        SQLModel.metadata.drop_all(self.engine)
        self.engine.dispose()

    def clear(self):
        self.records.clear()
        self.statements.clear()

    def seed(self, versions):
        self.latest, self.previous = {}, {}
        self.history_count = 3 * versions + 1
        with Session(self.engine) as session:
            session.exec(delete(GeneratedDocument))
            for kind in ("tailored_resume", "cover_letter", "selection_criteria"):
                for version in range(versions):
                    # The final two versions deliberately share a timestamp.
                    created = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=min(version, versions - 2))
                    document = GeneratedDocument(
                        user_id=self.owner, application_id=self.application_id, document_type=kind,
                        content="Current Department Officer. " + "x" * 8192,
                        trace_json=json.dumps({"padding": "x" * 65536}), created_at=created,
                    )
                    session.add(document)
                    session.flush()
                    self.previous[kind] = self.latest.get(kind)
                    self.latest[kind] = document.id
            unknown = GeneratedDocument(user_id=self.owner, application_id=self.application_id, document_type="legacy_note", content="Keep unknown types")
            session.add(unknown)
            session.flush()
            self.latest[unknown.document_type] = unknown.id
            session.add(GeneratedDocument(user_id=uuid4(), application_id=self.application_id, document_type="tailored_resume", content="Other owner", created_at=datetime(2030, 1, 1, tzinfo=timezone.utc)))
            other = JobApplication(user_id=self.owner, company="Other Department", position_title="Other", job_description="Other")
            session.add(other)
            session.flush()
            session.add(GeneratedDocument(user_id=self.owner, application_id=other.id, document_type="tailored_resume", content="Other application", created_at=datetime(2030, 1, 1, tzinfo=timezone.utc)))
            session.commit()
        self.clear()

    def document_reads(self):
        return [r for r in self.records if "from generateddocument" in r["sql"].lower()]

    def test_latest_query_limits_rows_and_bytes_as_history_grows(self):
        sizes = []
        for versions in (30, 100):
            self.seed(versions)
            with Session(self.engine) as session:
                result = main.latest_application_documents(session, self.application_id, self.owner)
            reads = self.document_reads()
            self.assertEqual(len(reads), 1)
            self.assertEqual(reads[0]["rows"], len(self.latest))
            self.assertEqual({k: v.id for k, v in result.items()}, self.latest)
            sizes.append(reads[0]["bytes"])
            nested = [n for n in visitors.iterate(self.statements[-1]) if isinstance(n, Select) and n is not self.statements[-1]]
            self.assertTrue(nested, "Ranking must happen in SQL, not in Python")
            for query in nested:
                self.assertLessEqual({c.key for c in query.selected_columns}, {"id", "document_type", "rank"})
        # IDs/timestamps can differ in length; large history fields must not scale output.
        self.assertLess(abs(sizes[1] - sizes[0]), 100)

    def test_current_filter_never_falls_back_to_older_passing_document(self):
        resume = Resume(source_text="Alex", ckb_json="[]")
        profile = ApplicantProfile(first_name="Alex", last_name="Example", email="a@example.com", phone="0400000000")
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.release_state_json = '{"generation_contract_required":true}'
            current = generation_inputs_fingerprint(application, resume, profile)
            old = session.get(GeneratedDocument, self.previous["tailored_resume"])
            old.trace_json, old.reviewer_json = json.dumps({"input_fingerprint": current}), '{"status":"pass"}'
            latest = session.get(GeneratedDocument, self.latest["tailored_resume"])
            latest.trace_json, latest.reviewer_json = '{"input_fingerprint":"stale"}', '{"status":"fail"}'
            session.commit()
        self.clear()
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            result = main.current_required_documents(session, application, self.owner, resume, profile)
            self.assertNotIn("tailored_resume", set(result))
        self.assertEqual(sum(r["rows"] for r in self.document_reads()), len(self.latest))

    def test_quality_release_and_pack_review_do_not_fetch_history(self):
        for operation, maximum in [("quality", 2), ("release", 3), ("pack", 1)]:
            with self.subTest(operation=operation):
                self.clear()
                with Session(self.engine) as session:
                    if operation == "quality":
                        main.quality_check(self.application_id, session, self.owner)
                    elif operation == "release":
                        main.release_checklist(self.application_id, "docx", "classic", session, self.owner)
                    else:
                        final = QualityCheckResponse(ready=True, issues=[], checked_documents=["tailored_resume", "cover_letter"])
                        with patch("app.main.quality_check", return_value=final), patch("app.main.review_application_pack", return_value={"status": "pass", "blocks_release": False}):
                            main.pack_review(self.application_id, session, self.owner)
                reads = self.document_reads()
                self.assertEqual(len(reads), maximum)
                self.assertTrue(all(r["rows"] == len(self.latest) for r in reads))

    def test_pack_completion_preserves_required_and_current_contract(self):
        payload = GenerateRequest(application_id=self.application_id, document_type="cover_letter", pack_id=self.pack)
        cases = [({}, False, False), ({"tailored_resume": {"status": "completed"}}, False, True), ({"tailored_resume": {"status": "completed"}}, True, False)]
        for requests, require_contract, completes in cases:
            with self.subTest(require_contract=require_contract, completes=completes):
                with Session(self.engine) as session:
                    application = session.get(JobApplication, self.application_id)
                    application.release_state_json = json.dumps({"generation_contract_required": require_contract, "generation_requests": {f"{self.pack}:{k}": v for k, v in requests.items()}})
                    session.commit()
                self.clear()
                with Session(self.engine) as session, patch.object(main.settings, "deployment_mode", "online"), patch("app.main.complete_pack_credits") as complete:
                    main.complete_generation_pack_if_ready(session, payload, self.owner)
                    self.assertEqual(complete.call_count, int(completes))
                self.assertTrue(all(r["rows"] == len(self.latest) for r in self.document_reads()))

    def test_history_endpoint_still_returns_all_versions(self):
        with Session(self.engine) as session:
            history = main.document_history(self.application_id, session, self.owner)
        self.assertEqual(len(history), self.history_count)
        self.assertEqual(sum(r["rows"] for r in self.document_reads()), self.history_count)

    def test_empty_scope_and_local_user_filter_contract(self):
        with Session(self.engine) as session:
            self.assertEqual(main.latest_application_documents(session, -1, self.owner), {})
            local = main.latest_application_documents(session, self.application_id, None)
            self.assertEqual(local["tailored_resume"].content, "Other owner")
