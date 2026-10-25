import json
import unittest
from collections import Counter
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

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
