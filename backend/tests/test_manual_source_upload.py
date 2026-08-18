import hashlib
import json
import unittest
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from docx import Document
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.auth import get_current_user
from app.database import get_session
from app.main import app


def pdf_payload(pages=1):
    stream = BytesIO()
    document = canvas.Canvas(stream)
    for page in range(pages):
        document.drawString(50, 750, f"Position description page {page + 1} with application criteria and duties.")
        document.showPage()
    document.save()
    return stream.getvalue()


def docx_payload():
    document = Document()
    document.add_paragraph("Job description duties and selection criteria from DOCX.")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


class ManualSourceUploadTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def create_application(self, text="Refer to the attached JDF."):
        response = self.client.post("/applications", json={
            "company": "Example Agency", "position_title": "Project Officer", "job_description": text,
        })
        self.assertEqual(response.status_code, 200)
        return response.json()

    def upload(self, application_id, payload, filename, content_type, source_type="job_description_attachment", target=None):
        data = {"expected_source_type": source_type}
        if target:
            data["target_source_id"] = target
        return self.client.post(
            f"/applications/{application_id}/sources/upload",
            data=data,
            files={"file": (filename, payload, content_type)},
        )

    def unresolved(self, application_id):
        sources = self.client.get(f"/applications/{application_id}/sources").json()
        return next(source for source in sources if source["source_type"] == "job_description_attachment")

    def test_manual_pdf_fulfils_unresolved_jdf_without_losing_provenance_or_binary(self):
        application = self.create_application()
        before = self.unresolved(application["id"])
        payload = pdf_payload()

        response = self.upload(application["id"], payload, "JDF.pdf", "application/pdf", target=before["source_id"])
        after = next(source for source in response.json() if source["source_id"] == before["source_id"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual((after["acquisition_status"], after["extraction_status"]), ("uploaded", "extracted"))
        self.assertIn("Position description", after["extracted_text"])
        self.assertEqual(after["discovered_from_source_id"], before["discovered_from_source_id"])
        self.assertEqual(after["content_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertNotIn("payload", after)

    def test_manual_docx_and_txt_uploads_create_extracted_sources(self):
        application = self.create_application("Submit your application online.")
        docx = self.upload(application["id"], docx_payload(), "position.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        txt = self.upload(application["id"], b"Application information and submission instructions.", "instructions.txt", "text/plain", "application_instruction_attachment")

        self.assertEqual(docx.status_code, 200)
        self.assertEqual(txt.status_code, 200)
        sources = txt.json()
        self.assertTrue(any(source["filename"] == "position.docx" and source["extraction_status"] == "extracted" for source in sources))
        self.assertTrue(any(source["filename"] == "instructions.txt" and source["acquisition_status"] == "uploaded" for source in sources))

    def test_manual_upload_enforces_ownership(self):
        owner_id, other_id = uuid4(), uuid4()
        app.dependency_overrides[get_current_user] = lambda: owner_id
        application = self.create_application()
        app.dependency_overrides[get_current_user] = lambda: other_id

        response = self.upload(application["id"], pdf_payload(), "JDF.pdf", "application/pdf")

        self.assertEqual(response.status_code, 404)

    def test_oversized_mismatched_and_corrupt_uploads_are_rejected(self):
        application = self.create_application()
        target = self.unresolved(application["id"])["source_id"]
        oversized = self.upload(application["id"], b"x" * (10 * 1024 * 1024 + 1), "JDF.pdf", "application/pdf", target=target)
        mismatch = self.upload(application["id"], docx_payload(), "JDF.pdf", "application/pdf", target=target)
        corrupt = self.upload(application["id"], b"%PDF-corrupt", "JDF.pdf", "application/pdf", target=target)

        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(corrupt.status_code, 400)
        unchanged = self.unresolved(application["id"])
        self.assertEqual((unchanged["acquisition_status"], unchanged["extraction_status"]), ("discovered", "not_attempted"))
        self.assertEqual(unchanged["extracted_text"], "")

    def test_duplicate_sha_is_persisted_without_duplicate_extracted_text(self):
        application = self.create_application("Submit your application online.")
        payload = b"Application instructions supplied in a text document."
        first = self.upload(application["id"], payload, "one.txt", "text/plain", "application_instruction_attachment")
        second = self.upload(application["id"], payload, "two.txt", "text/plain", "application_instruction_attachment")

        self.assertEqual(first.status_code, 200)
        duplicates = [source for source in second.json() if source["content_sha256"] == hashlib.sha256(payload).hexdigest()]
        self.assertEqual(len(duplicates), 2)
        self.assertEqual([source["extraction_status"] for source in duplicates], ["extracted", "not_applicable"])
        self.assertEqual(duplicates[1]["extracted_text"], "")
        self.assertIn("Duplicate content", json.loads(duplicates[1]["warnings_json"])[0])

    def test_partial_upload_is_truthfully_persisted(self):
        application = self.create_application()
        target = self.unresolved(application["id"])["source_id"]
        extracted = {
            "filename": "long.pdf", "content_type": "application/pdf", "content_sha256": "b" * 64,
            "extracted_text": "Only the bounded pages.", "extraction_status": "partial",
            "warnings_json": json.dumps(["Only the first 50 PDF pages were processed."]),
        }
        with patch("app.main.process_uploaded_document", return_value=extracted):
            response = self.upload(application["id"], b"%PDF-test", "long.pdf", "application/pdf", target=target)
        source = next(item for item in response.json() if item["source_id"] == target)

        self.assertEqual(source["acquisition_status"], "uploaded")
        self.assertEqual(source["extraction_status"], "partial")
        self.assertIn("first 50", json.loads(source["warnings_json"])[0])

    def test_backend_rejects_invalid_type_and_mismatched_target(self):
        application = self.create_application()
        target = self.unresolved(application["id"])["source_id"]

        invalid = self.upload(application["id"], pdf_payload(), "form.pdf", "application/pdf", "mandatory_form")
        mismatch = self.upload(application["id"], pdf_payload(), "pack.pdf", "application/pdf", "application_instruction_attachment", target)

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(mismatch.status_code, 400)


if __name__ == "__main__":
    unittest.main()
