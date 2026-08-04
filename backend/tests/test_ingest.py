import unittest
from io import BytesIO

from docx import Document

from app.ingest import extract_resume_text, parse_job_ad_text, parse_job_page


class IngestTests(unittest.TestCase):
    def test_extracts_master_resume_from_docx(self):
        document = Document()
        document.add_heading("Alex Morgan", level=1)
        document.add_paragraph("Project coordination and administration experience across complex projects.")
        stream = BytesIO()
        document.save(stream)

        text = extract_resume_text("resume.docx", stream.getvalue())

        self.assertIn("Alex Morgan", text)
        self.assertIn("Project coordination", text)

    def test_rejects_unsupported_resume_file(self):
        with self.assertRaises(ValueError):
            extract_resume_text("resume.png", b"not a supported resume document" * 3)

    def test_reads_structured_job_posting(self):
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"JobPosting","title":"Project Support Officer",
         "hiringOrganization":{"@type":"Organization","name":"Example Energy"},
         "description":"<p>Coordinate projects and prepare reports.</p>"}
        </script></head></html>
        """

        result = parse_job_page(html, "https://example.com/job/1")

        self.assertEqual(result["position_title"], "Project Support Officer")
        self.assertEqual(result["company"], "Example Energy")
        self.assertIn("Coordinate projects", result["job_description"])
        self.assertEqual(result["source"], "structured_job_posting")

    def test_separates_seek_style_full_job_ad(self):
        raw_text = """
        **Project Support Officer / Junior Project Manager**

        BayWa r.e. Solar Systems Pty Ltd
        Bibra Lake, Perth WA
        Full time

        **About the Role**
        We are seeking a highly organised project support professional to coordinate documentation,
        schedules, stakeholders, procurement and logistics across renewable energy projects.

        **Key Responsibilities**
        Maintain project registers and prepare progress reports for management review.
        """

        result = parse_job_ad_text(raw_text)

        self.assertEqual(result["position_title"], "Project Support Officer / Junior Project Manager")
        self.assertEqual(result["company"], "BayWa r.e. Solar Systems Pty Ltd")
        self.assertEqual(result["warnings"], [])

    def test_warns_when_old_job_content_is_mixed_in(self):
        raw_text = """
        Project Administrator
        Sync Energy
        Perth WA
        About the Role
        Support Fexey battery projects by maintaining schedules, registers and reports.
        About BayWa r.e. Solar Systems
        This older advertisement describes a different solar distribution position and company.
        """

        result = parse_job_ad_text(raw_text, ["BayWa r.e. Solar Systems"])

        self.assertTrue(any("earlier saved job" in warning for warning in result["warnings"]))

    def test_ignores_seek_buttons_and_accepts_western_australia_in_company_name(self):
        raw_text = """
        Program Coordinator
        Minerals Research Institute of Western Australia
        View all jobs
        Share or report ad
        East Perth, Perth WA (Hybrid)
        Full time
        Apply
        Save
        The Position
        Coordinate education and workforce programs, stakeholder relationships, reporting and research scholarships across Western Australia.
        """

        result = parse_job_ad_text(raw_text)

        self.assertEqual(result["company"], "Minerals Research Institute of Western Australia")

    def test_finds_company_from_job_body_when_header_is_missing(self):
        raw_text = """
        Contract Projects Administrators – Perth and Adelaide
        Metrowest is growing and we're looking for capable Projects Administrators to support our project delivery team in a varied and fast-paced role.
        You'll work closely with the project team to keep jobs running smoothly, from purchase orders and invoicing through to document control and reporting.
        What you'll be doing
        Raising purchase orders, maintaining job records, processing invoices and coordinating project documentation.
        What we're looking for
        Strong administration experience in construction, engineering or a similar project environment.
        Why join Metrowest
        Stable, growing business with a long-term project pipeline and supportive team.
        """

        result = parse_job_ad_text(raw_text)

        self.assertEqual(result["company"], "Metrowest")
        self.assertNotEqual(result["company"], "What you'll be doing")


if __name__ == "__main__":
    unittest.main()
