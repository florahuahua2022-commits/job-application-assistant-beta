from typing import Any, Callable


def evaluate_labelled_cases(
    cases: list[dict[str, Any]],
    detector: Callable[[dict[str, Any]], str],
    *,
    repetitions: int = 5,
) -> dict[str, Any]:
    runs = [[detector(case) for case in cases] for _ in range(repetitions)]
    expected_risks = [index for index, case in enumerate(cases) if case["expected"] != "none"]
    negatives = [index for index, case in enumerate(cases) if case["expected"] == "none"]
    first = runs[0]
    recalled = sum(first[index] == cases[index]["expected"] for index in expected_risks)
    blocking_false_positives = sum(first[index] == "error" for index in negatives)
    warning_false_positives = sum(first[index] == "warning" for index in negatives)
    consistent = sum(
        len({run[index] for run in runs}) == 1
        for index in range(len(cases))
    )
    return {
        "case_count": len(cases),
        "risk_case_count": len(expected_risks),
        "negative_case_count": len(negatives),
        "known_risk_recall": recalled / len(expected_risks) if expected_risks else 1.0,
        "blocking_false_positive_rate": blocking_false_positives / len(negatives) if negatives else 0.0,
        "warning_false_positive_rate": warning_false_positives / len(negatives) if negatives else 0.0,
        "severity_consistency": consistent / len(cases) if cases else 1.0,
        "runs": repetitions,
        "results": [
            {"id": case["id"], "expected": case["expected"], "actual": first[index]}
            for index, case in enumerate(cases)
        ],
    }
