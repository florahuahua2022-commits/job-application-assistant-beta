import unittest
from io import BytesIO
from zipfile import ZipFile

from pypdf import PdfReader

from app.exporter import create_docx, create_pdf


SAMPLE = """ALEX MORGAN
Perth, WA | contact@example.com

**PROFESSIONAL SUMMARY**

Experienced administrative professional.

**CORE CAPABILITIES**

- Executive support
- Records management
"""


class ExporterTests(unittest.TestCase):
    def test_docx_is_valid_ooxml(self):
        payload = create_docx(SAMPLE, "Tailored Resume")
        self.assertTrue(payload.startswith(b"PK"))
        with ZipFile(BytesIO(payload)) as archive:
            self.assertIn("word/document.xml", archive.namelist())

    def test_pdf_is_readable_and_contains_text(self):
        payload = create_pdf(SAMPLE, "Tailored Resume")
        self.assertTrue(payload.startswith(b"%PDF"))
        reader = PdfReader(BytesIO(payload))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("ALEX MORGAN", text)
        self.assertIn("Executive support", text)


if __name__ == "__main__":
    unittest.main()
