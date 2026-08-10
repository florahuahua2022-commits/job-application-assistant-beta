"""Run the configured application through up to five paid, real generation rounds.

Required environment variables:
  DTMI_APPLICATION_ID, API_BASE_URL
Optional for online mode:
  API_BEARER_TOKEN

The script never prints the bearer token and writes a JSON report containing
quality results plus per-document generation traces and recorded cost telemetry.
"""
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def request_json(base: str, path: str, token: str, *, method: str = "GET", body: dict | None = None):
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(base.rstrip("/") + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed ({error.code}): {detail}") from error


def main() -> int:
    base = os.getenv("API_BASE_URL", "http://localhost:8000")
    token = os.getenv("API_BEARER_TOKEN", "")
    application_id = os.getenv("DTMI_APPLICATION_ID", "").strip()
    try:
        round_limit = int(os.getenv("LIVE_REGRESSION_ROUNDS", "5"))
    except ValueError:
        round_limit = 0
    if not 1 <= round_limit <= 5:
        print("Set LIVE_REGRESSION_ROUNDS to a number from 1 to 5.", file=sys.stderr)
        return 2
    if not application_id.isdigit():
        print("Set DTMI_APPLICATION_ID to the saved DTMI test application ID.", file=sys.stderr)
        return 2
    applications = request_json(base, "/applications", token)
    application = next((item for item in applications if item["id"] == int(application_id)), None)
    if not application:
        print("The configured DTMI application was not found.", file=sys.stderr)
        return 2
    document_types = ["tailored_resume", "cover_letter"]
    if (application.get("selection_criteria") or "").strip():
        document_types.append("selection_criteria")

    output = Path(sys.argv[1] if len(sys.argv) > 1 else "dtmi-live-regression-report.json")
    rounds = []

    def write_report(status: str, error: str = "") -> dict:
        report = {
            "schema_version": "1.1",
            "status": status,
            "application_id": int(application_id),
            "requested_round_count": round_limit,
            "completed_round_count": len(rounds),
            "all_rounds_ready": len(rounds) == round_limit and all(
                (item.get("quality_check") or {}).get("ready", False) for item in rounds
            ),
            "rounds": rounds,
        }
        if error:
            report["error"] = error
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    for round_number in range(1, round_limit + 1):
        pack_id = str(uuid4())
        generated = []
        try:
            for document_type in document_types:
                document = request_json(
                    base, "/generate", token, method="POST",
                    body={"application_id": int(application_id), "document_type": document_type, "pack_id": pack_id},
                )
                trace = request_json(base, f"/documents/{document['id']}/trace", token)
                generated.append({
                    "document_id": document["id"],
                    "document_type": document_type,
                    "content": document["content"],
                    "trace": trace,
                })
        except RuntimeError as error:
            rounds.append({"round": round_number, "pack_id": pack_id, "documents": generated})
            write_report("failed", str(error))
            print(f"Regression stopped in round {round_number}: {error}", file=sys.stderr)
            return 1
        quality = request_json(base, f"/applications/{application_id}/quality-check", token)
        rounds.append({"round": round_number, "pack_id": pack_id, "quality_check": quality, "documents": generated})
        write_report("running")
        print(f"Completed regression round {round_number}/{round_limit}; ready={quality['ready']}")

    report = write_report("complete")
    print(f"Report written to {output.resolve()}")
    return 0 if report["all_rounds_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
