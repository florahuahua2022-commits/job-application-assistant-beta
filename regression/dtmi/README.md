# DTMI P0 release regression

`labelled_cases.json` is the human-labelled deterministic gate derived from the
10 August 2026 DTMI Project Officer product test. The automated test runs every
case five times and enforces:

- known P0 risk recall: 100%;
- Blocking false-positive rate: no more than 2%;
- Warning false-positive rate: no more than 10%;
- severity consistency: at least 95%.

This fixture gate does not count as the required five real model generations.
To run the paid live check, start the application with a saved DTMI test case,
configure the model provider and run from `backend`:

```powershell
$env:API_BASE_URL="http://localhost:8000"
$env:DTMI_APPLICATION_ID="123"
python scripts/run_dtmi_live_regression.py ..\regression\dtmi\live-report.json
```

For online mode, also set `API_BEARER_TOKEN` for the test user. The report
contains all generated documents, Content Check results, generation traces,
token usage and estimated cost. Do not commit a live report containing personal
resume data.
