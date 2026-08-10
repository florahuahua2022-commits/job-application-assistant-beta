import json
import unittest
from pathlib import Path

from app.fact_boundaries import find_fact_boundary_issues
from app.jd_similarity import find_jd_similarity_issues
from app.main import document_release_blockers
from app.models import ApplicantProfile, GeneratedDocument, JobApplication
from app.regression_metrics import evaluate_labelled_cases


ROOT = Path(__file__).resolve().parents[2]


def detect(case: dict) -> str:
    if case["detector"] == "fact":
        issues = find_fact_boundary_issues(
            case["content"], case.get("evidence", ""),
            motivation_confirmed=case.get("motivation_confirmed", False),
        )
    elif case["detector"] == "jd":
        issues = find_jd_similarity_issues(case["content"], case.get("jd", ""))
    else:
        application = JobApplication(
            company="Example Agency",
            position_title=case.get("position_title", "Project Officer"),
            job_description=case.get("jd", "Coordinate projects."),
        )
        profile = ApplicantProfile(
            first_name="Alex", last_name="Morgan", phone="0400000000", email="alex@example.com",
            work_rights=case.get("work_rights", "not_specified"),
            work_rights_confirmed=case.get("work_rights_confirmed", False),
            availability_notice=case.get("availability", "not_specified"),
            availability_confirmed=case.get("availability_confirmed", False),
        )
        document = GeneratedDocument(
            application_id=1, document_type="cover_letter", content=case["content"],
            reviewer_json='{"status":"pass","results":[]}',
        )
        blockers = document_release_blockers(document, application, profile, evidence_text=case.get("evidence", ""))
        issues = [{"severity": "error"}] if blockers else []
    if any(issue["severity"] == "error" for issue in issues):
        return "error"
    if any(issue["severity"] == "warning" for issue in issues):
        return "warning"
    return "none"


class DTMIRegressionTests(unittest.TestCase):
    def test_human_labelled_dtmi_release_gate_metrics(self):
        dataset = json.loads(
            (ROOT / "regression" / "dtmi" / "labelled_cases.json").read_text(encoding="utf-8")
        )
        metrics = evaluate_labelled_cases(dataset["cases"], detect, repetitions=5)

        self.assertEqual(dataset["dataset_version"], "1.0")
        self.assertEqual(metrics["known_risk_recall"], 1.0)
        self.assertLessEqual(metrics["blocking_false_positive_rate"], 0.02)
        self.assertLessEqual(metrics["warning_false_positive_rate"], 0.10)
        self.assertGreaterEqual(metrics["severity_consistency"], 0.95)
        self.assertEqual(metrics["runs"], 5)


if __name__ == "__main__":
    unittest.main()
