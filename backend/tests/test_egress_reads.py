import json
import unittest
from collections import Counter
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import Session, SQLModel, select

from app import main
from app.models import GeneratedDocument, JobAdParseRequest, JobApplication
from egress_probe import measured_engine


class IdentityReadTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.engine = measured_engine(self.records)
        SQLModel.metadata.create_all(self.engine)
        self.owner = uuid4()
        self.companies = ["Current Department"] + ["North Rail"] * 2 + [f"Agency {i}" for i in range(18)]
        with Session(self.engine) as session:
            for index, company in enumerate(self.companies):
                session.add(JobApplication(
                    user_id=self.owner, company=company, position_title="Transport Planner",
                    job_description="Coordinate projects.",
                    archived_at=datetime(2026, 1, 1) if index == 1 else None,
                ))
            session.add(JobApplication(user_id=uuid4(), company="Foreign Owner", position_title="Private", job_description="Private"))
            session.commit()
            self.application_id = session.exec(select(JobApplication.id).where(JobApplication.user_id == self.owner).order_by(JobApplication.id)).first()
            for kind, content in [("tailored_resume", "Current Department Transport Planner"), ("cover_letter", "Current Department Transport Planner. North Rail Transport Planner.")]:
                session.add(GeneratedDocument(user_id=self.owner, application_id=self.application_id, document_type=kind, content=content))
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def padding(self, size):
        with Session(self.engine) as session:
            for application in session.exec(select(JobApplication)).all():
                for name in ("resume_snapshot_json", "evidence_matches_json", "job_model_json"):
                    setattr(application, name, json.dumps({"padding": "x" * size}))
                session.add(application)
            session.commit()
        self.records.clear()

    def test_parse_ad_reads_only_company_preserving_scope_duplicates_and_archive(self):
        totals = []
        for size in (65536, 262144):
            self.padding(size)
            with Session(self.engine) as session, patch("app.main.parse_job_ad_text", wraps=main.parse_job_ad_text) as parser:
                result = main.parse_application_ad(JobAdParseRequest(raw_text=(
                    "Project Officer\nCurrent Department\nCoordinate projects, maintain registers, "
                    "prepare reports and support stakeholders. Submit your CV and a cover letter."
                )), session, self.owner)
                self.assertTrue(result.application_requirements)
                self.assertEqual(Counter(parser.call_args.args[1]), Counter(self.companies))
            reads = [r for r in self.records if "FROM jobapplication" in r["sql"]]
            self.assertEqual(len(reads), 1)
            self.assertEqual(reads[0]["rows"], len(self.companies))
            self.assertEqual(reads[0]["columns"], ("company",))
            totals.append(reads[0]["bytes"])
        self.assertEqual(totals[0], totals[1])

    def test_cross_application_reads_only_identity_preserving_leak_detection(self):
        totals = []
        for size in (65536, 262144):
            self.padding(size)
            with Session(self.engine) as session:
                result = main.quality_check(self.application_id, session, self.owner)
            self.assertIn("cross_application_content_leak", [issue.code for issue in result.issues])
            reads = [r for r in self.records if "jobapplication.id !=" in r["sql"]]
            self.assertEqual(len(reads), 1)
            self.assertEqual(reads[0]["rows"], len(self.companies) - 1)
            self.assertEqual(reads[0]["columns"], ("company", "position_title"))
            totals.append(reads[0]["bytes"])
        self.assertEqual(totals[0], totals[1])

    def test_probe_counts_actual_fetches(self):
        self.records.clear()
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql("SELECT 1 AS first, 2 AS second UNION ALL SELECT 3, 4 UNION ALL SELECT 5, 6")
            self.assertEqual(result.fetchone(), (1, 2))
            self.assertEqual(result.fetchmany(1), [(3, 4)])
            self.assertEqual(result.fetchall(), [(5, 6)])
        self.assertEqual(self.records[-1]["rows"], 3)
        self.assertEqual(self.records[-1]["columns"], ("first", "second"))
        self.assertEqual(self.records[-1]["bytes"], 6)


class GenerationStatusReadTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.engine = measured_engine(self.records)
        SQLModel.metadata.create_all(self.engine)
        self.owner, self.pack = uuid4(), uuid4()
        self.key = f"{self.pack}:tailored_resume"
        with Session(self.engine) as session:
            application = JobApplication(user_id=self.owner, company="Agency", position_title="Officer", job_description="JD")
            session.add(application)
            session.commit()
            self.application_id = application.id
            document = GeneratedDocument(user_id=self.owner, application_id=application.id, document_type="tailored_resume", content="Saved draft")
            session.add(document)
            session.commit()
            self.document_id = document.id

    def tearDown(self):
        self.engine.dispose()

    def state(self, value, size=65536):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.release_state_json = value if isinstance(value, str) else json.dumps({"generation_requests": {self.key: value}})
            for name in ("resume_snapshot_json", "evidence_matches_json", "job_model_json"):
                setattr(application, name, json.dumps({"padding": "x" * size}))
            session.commit()
        self.records.clear()

    def poll(self, owner=None, application_id=None, pack=None, document_type="tailored_resume"):
        with Session(self.engine) as session:
            return main.generation_request_status(
                self.application_id if application_id is None else application_id,
                self.pack if pack is None else pack, document_type, session,
                self.owner if owner is None else owner,
            )

    def application_reads(self):
        return [r for r in self.records if "FROM jobapplication" in r["sql"]]

    def assert_read_only(self):
        self.assertFalse(any(r["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"} for r in self.records))

    def test_running_polls_fetch_only_state_and_ignore_large_padding(self):
        totals = []
        for size in (65536, 262144):
            self.state({"status": "running"}, size)
            for _ in range(10):
                self.assertEqual(self.poll(), {"status": "running"})
            reads = self.application_reads()
            self.assertEqual(len(reads), 10)
            self.assertEqual(sum(r["rows"] for r in reads), 10)
            self.assertTrue(all(r["columns"] == ("release_state_json",) for r in reads))
            self.assertFalse(any("FROM generateddocument" in r["sql"] for r in self.records))
            self.assert_read_only()
            totals.append(sum(r["bytes"] for r in reads))
        self.assertEqual(totals[0], totals[1])

    def test_status_variants_preserve_saved_document_and_never_change_credits(self):
        for status, has_document in [("completed", True), ("failed", True), ("failed", False), ("running", True)]:
            with self.subTest(status=status, has_document=has_document):
                value = {"status": status, "message": "Saved status", "detail": {"code": "example"}}
                if has_document:
                    value["document_id"] = self.document_id
                self.state(value)
                with patch("app.main.reserve_pack_credits") as reserve, patch("app.main.complete_pack_credits") as complete, patch("app.main.release_pack_credits") as release:
                    result = self.poll()
                for operation in (reserve, complete, release):
                    operation.assert_not_called()
                self.assertEqual({k: v for k, v in result.items() if k != "document"}, value)
                documents = [r for r in self.records if "FROM generateddocument" in r["sql"]]
                self.assertEqual(sum(r["rows"] for r in documents), int(has_document))
                if has_document:
                    self.assertEqual(result["document"].content, "Saved draft")
                else:
                    self.assertNotIn("document", result)
                self.assertEqual(self.application_reads()[0]["columns"], ("release_state_json",))
                self.assert_read_only()

    def test_unauthorized_poll_returns_no_application_rows(self):
        self.state({"status": "running"})
        with self.assertRaises(HTTPException) as error:
            self.poll(owner=uuid4())
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(sum(r["rows"] for r in self.application_reads()), 0)
        self.assertFalse(any("FROM generateddocument" in r["sql"] for r in self.records))
        self.assert_read_only()

    def test_missing_application_request_and_bad_json_remain_404(self):
        for kwargs, value in [({"application_id": -1}, {"status": "running"}), ({"pack": uuid4()}, {"status": "running"}), ({"document_type": "cover_letter"}, {"status": "running"}), ({}, "{}"), ({}, "[]"), ({}, "invalid")]:
            with self.subTest(kwargs=kwargs, value=value):
                self.state(value)
                with self.assertRaises(HTTPException) as error:
                    self.poll(**kwargs)
                self.assertEqual(error.exception.status_code, 404)
                self.assertTrue(all(r["columns"] == ("release_state_json",) for r in self.application_reads()))
                self.assert_read_only()

    def test_local_mode_without_user_filter_still_reads_state(self):
        self.state({"status": "running"})
        with Session(self.engine) as session:
            self.assertEqual(main.generation_request_status(self.application_id, self.pack, "tailored_resume", session, None), {"status": "running"})
        self.assertEqual(self.application_reads()[0]["columns"], ("release_state_json",))
