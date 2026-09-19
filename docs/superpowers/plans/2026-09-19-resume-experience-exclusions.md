# Master Resume Experience Exclusions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist reversible user exclusions for uncovered Resume experiences so intentional omissions remain visible and auditable without blocking application work.

**Architecture:** Keep deterministic candidate discovery in `ingest.py`, add one normalized-anchor candidate ID and one reconciliation function, then make every integrity caller consume the Resume's reconciled exclusion list. Persist exclusions on `Resume`, include them in application snapshots, and expose one validated PATCH action used by the existing Master Resume review UI.

**Tech Stack:** Python 3, FastAPI, SQLModel/SQLAlchemy, PostgreSQL/SQLite, Next.js/React/TypeScript, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-19-resume-experience-exclusions-design.md`

## Global Constraints

- `candidate_id` uses only normalized `organization`, `role_title`, and `time_period_text`; no semantic or fuzzy matching.
- All three anchors unchanged preserves an exclusion; any one anchor changed invalidates it and produces a new unresolved candidate.
- Existing parsed-experience `needs_review` flags remain blocking and cannot be dismissed.
- Excluded candidates stay out of `experiences_json`, CKB, and generated documents.
- Application snapshots include exclusions; any exclusion change after snapshot creation makes that snapshot stale and requires Update to latest.
- Exclusion actions must preserve source text, structured experiences, CKB facts, and unsaved editor input.
- Risk scan is deterministic and makes no LLM calls.
- Unresolved candidates allow create/upload/edit persistence with an explicit unresolved count; they still block Update to latest, diagnosis, and generation.

## Review Focus

- Malformed legacy `experience_exclusions_json` must fail closed as no exclusions rather than crashing or suppressing candidates; Task 1 tests it.
- A client-supplied candidate ID that does not exist in the current source must be rejected for `exclude`; Task 2 tests it.
- Two scans and two identical exclude actions must not reorder or duplicate exclusions; Task 2 tests it.
- A pre-exclusion application snapshot must remain stale even though source and experiences are unchanged; Task 3 tests it.
- A failed exclusion request must not discard current Master Resume form edits; Task 4 tests state preservation.

---

### Task 1: Stable Candidate Identity, Persistence, and Reconciliation

**Files:**
- Modify: `backend/app/ingest.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/database.py`
- Test: `backend/tests/test_ingest.py`
- Test: `backend/tests/test_database_migrations.py` if present; otherwise `backend/tests/test_real_user_regression.py`

**Interfaces:**
- Produces: `experience_candidate_id(organization: object, role_title: object, time_period_text: object) -> str`
- Produces: `find_uncovered_experience_candidates(source_text: str, experiences: list[dict], exclusions: list[dict] | None = None) -> list[dict]`, with `candidate_id` and `status` on every returned candidate.
- Produces: `reconcile_experience_exclusions(source_text: str, experiences: list[dict], exclusions_json: str) -> str` containing only exact-ID current exclusions in deterministic candidate order.
- Produces: `Resume.experience_exclusions_json: str = "[]"`; ordinary create/update payloads cannot write this field.
- Produces: `Resume.experience_exclusions_json: str = "[]"` plus matching create/update payload fields.

- [ ] **Step 1: Write anchor identity and three-state failing tests**

Add focused tests to `backend/tests/test_ingest.py` using the real header form:

```python
source = """Work Experience
Sodex: Utility, December 2023 - April 2024.
Puma, Port Hedland: service station work, March 2023 - May 2023.
Education"""

def test_candidate_id_changes_only_when_an_anchor_changes():
    base = experience_candidate_id("Sodex", "Utility", "December 2023 - April 2024")
    assert base == experience_candidate_id(" sodex ", "Utility", "December 2023 – April 2024")
    assert base != experience_candidate_id("Sodex WA", "Utility", "December 2023 - April 2024")
    assert base != experience_candidate_id("Sodex", "Cleaner", "December 2023 - April 2024")
    assert base != experience_candidate_id("Sodex", "Utility", "January 2024 - April 2024")

def test_exclusion_preserves_wording_edits_but_not_anchor_edits():
    candidate = find_uncovered_experience_candidates(source, [])[0]
    exclusion = [{**candidate, "status": "excluded_by_user"}]
    assert find_uncovered_experience_candidates(source.replace(".\nPuma", " - casual duties.\nPuma"), [], exclusion)[0]["status"] == "excluded_by_user"
    changed = source.replace("Utility", "Cleaner")
    candidates = find_uncovered_experience_candidates(changed, [], exclusion)
    assert candidates[0]["status"] == "unresolved"
    assert candidates[0]["candidate_id"] != candidate["candidate_id"]
```

Also test covered candidates are absent, malformed exclusion JSON reconciles to `[]`, unmatched exclusions are dropped, and candidate ordering is stable.

- [ ] **Step 2: Run the focused tests and record RED**

Run: `cd backend; .venv/Scripts/python.exe -m pytest tests/test_ingest.py -k "candidate_id or exclusion" -q`

Expected: FAIL because the new function, parameter, candidate state, and reconciliation behavior do not exist.

- [ ] **Step 3: Implement the minimum deterministic identity/state functions**

In `backend/app/ingest.py`, reuse `_experience_identity_value`; hash the three normalized anchors with `hashlib.sha1` and a fixed prefix. Extend candidate dictionaries with `candidate_id` and exact-match `status`. Implement reconciliation by parsing a list, indexing valid exclusions by candidate ID, and returning only exclusions still matched by current uncovered candidates.

Do not compare responsibility, source excerpt, or whole-source hashes. Do not introduce another parser class.

- [ ] **Step 4: Add model and startup migration support**

Add `experience_exclusions_json: str = "[]"` to `Resume` only. Exclusions are writable solely through the validated PATCH route. Add `ALTER TABLE resume ADD COLUMN experience_exclusions_json TEXT NOT NULL DEFAULT '[]'` for PostgreSQL and the SQLite equivalent following the current `experiences_json`/`ckb_json` migration pattern.

- [ ] **Step 5: Run focused and migration tests GREEN**

Run: `cd backend; .venv/Scripts/python.exe -m pytest tests/test_ingest.py tests/test_real_user_regression.py -k "candidate_id or exclusion or migration" -q`

Expected: PASS; unchanged existing coverage tests remain green.

- [ ] **Step 6: Commit Task 1**

```powershell
git add backend/app/ingest.py backend/app/models.py backend/app/database.py backend/tests/test_ingest.py backend/tests/test_real_user_regression.py
git commit -m "Add stable Resume experience exclusion state"
```

### Task 2: Risk Scan and Validated Exclusion API

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_real_user_regression.py`

**Interfaces:**
- Consumes: Task 1 candidate IDs, candidate states, and reconciliation.
- Produces: `ExperienceExclusionUpdate(candidate_id: str, action: Literal["exclude", "restore"])`.
- Produces: `PATCH /resumes/{resume_id}/experience-exclusions` returning the updated Resume and current review candidates.
- Produces: `resume_review_experiences` responses whose uncovered entries contain `candidate_id` and `status`.

- [ ] **Step 1: Write failing API/risk-scan tests first**

Add tests that:

```python
scan = client.post("/resumes/risk-scan")
candidate = scan.json()["resumes"][0]["experiences"][0]
assert candidate["status"] == "unresolved"

excluded = client.patch(f"/resumes/{resume_id}/experience-exclusions", json={
    "candidate_id": candidate["candidate_id"], "action": "exclude",
})
assert excluded.status_code == 200
assert json.loads(excluded.json()["resume"]["experience_exclusions_json"])[0]["status"] == "excluded_by_user"

second = client.patch(...same payload...)
assert second.json() == excluded.json()
```

Assert an invented candidate ID returns 422/409 without changing the Resume; restore removes the exclusion and makes the candidate unresolved again; two scans have identical exclusion values and order; risk-scan `needs_review_count` ignores excluded candidates but still returns them for UI display; `source_text`, `experiences_json`, and `ckb_json` are byte-for-byte unchanged.

- [ ] **Step 2: Run API tests and record RED**

Run: `cd backend; .venv/Scripts/python.exe -m pytest tests/test_real_user_regression.py -k "experience_exclusion or excluded_candidate" -q`

Expected: FAIL with missing route/model/state behavior.

- [ ] **Step 3: Centralize exclusion loading and integrity filtering**

In `main.py`, add one small parser for `resume.experience_exclusions_json`, reconcile it before candidate checks, and update `master_resume_integrity_issue` plus `resume_review_experiences` so only `status == "unresolved"` blocks. Keep excluded candidates in the risk-scan response under an explicit `excluded_experiences` collection or a single collection with status; use one contract consistently in frontend tests.

- [ ] **Step 4: Implement the validated PATCH route**

For `exclude`, regenerate current uncovered candidates without trusting client anchors, locate the exact candidate ID, and persist the server-derived candidate record. Reject if absent. For `restore`, remove the exact ID idempotently. Reconcile before writing, update `updated_at`, invalidate evidence matches, and do not rebuild or alter CKB.

- [ ] **Step 5: Run focused API/risk-scan tests GREEN**

Run: `cd backend; .venv/Scripts/python.exe -m pytest tests/test_real_user_regression.py -k "risk_scan or experience_exclusion or excluded_candidate" -q`

Expected: PASS, including byte-for-byte preservation and idempotency assertions.

- [ ] **Step 6: Commit Task 2**

```powershell
git add backend/app/main.py backend/app/models.py backend/tests/test_real_user_regression.py
git commit -m "Add validated Resume experience exclusion API"
```

### Task 3: Application Snapshot, Update-to-Latest, Diagnosis, and Generate Gates

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_content_quality_v2.py`
- Test: `backend/tests/test_real_user_regression.py`

**Interfaces:**
- Consumes: `Resume.experience_exclusions_json` and Task 2 shared integrity behavior.
- Produces: `resume_snapshot()` output containing `experience_exclusions_json`.
- Produces: snapshot freshness comparison across source text, experiences, and exclusions.

- [ ] **Step 1: Write snapshot-after-exclusion RED test**

Create an application snapshot while a candidate is unresolved, then exclude the candidate on the Master Resume. Assert:

```python
blocked = client.post("/generate", json={"application_id": app_id, "document_type": "tailored_resume"})
assert blocked.status_code == 409
assert blocked.json()["detail"]["code"] == "application_resume_snapshot_outdated"

updated = client.put(f"/applications/{app_id}/resume", json={
    "use_latest_master": True,
    "expected_snapshot": old_snapshot,
})
assert updated.status_code == 200
snapshot = json.loads(updated.json()["resume_snapshot_json"])
assert json.loads(snapshot["experience_exclusions_json"])[0]["candidate_id"] == candidate_id
```

Also assert restore after snapshot makes it stale, unresolved candidates still block Update to latest, excluded candidates allow it, and diagnosis/generate share the same outcome.

- [ ] **Step 2: Run snapshot/generation tests and record RED**

Run: `cd backend; .venv/Scripts/python.exe -m pytest tests/test_content_quality_v2.py tests/test_real_user_regression.py -k "snapshot and exclusion or update_to_latest and excluded or generate and excluded" -q`

Expected: FAIL because snapshots neither store nor compare exclusion state.

- [ ] **Step 3: Include exclusions in snapshot construction and freshness**

Update `resume_snapshot`, `application_master_resume`, and `stale_resume_snapshot_detail` to carry and compare `experience_exclusions_json`. Do not add a special case that treats matching source/experiences as current when exclusions differ.

- [ ] **Step 4: Route Update-to-latest and generation checks through shared integrity logic**

Pass the snapshot exclusion state when reconstructing a Resume. Verify every diagnosis/generation entry already calls `master_resume_integrity_issue`; replace any direct uncovered-candidate call with the shared helper rather than adding endpoint-specific exceptions.

- [ ] **Step 5: Run snapshot/generation tests GREEN**

Run: `cd backend; .venv/Scripts/python.exe -m pytest tests/test_content_quality_v2.py tests/test_real_user_regression.py -k "snapshot or update_to_latest or generate or diagnosis" -q`

Expected: PASS with the pre-exclusion snapshot test proving Update to latest is mandatory.

- [ ] **Step 6: Commit Task 3**

```powershell
git add backend/app/main.py backend/tests/test_content_quality_v2.py backend/tests/test_real_user_regression.py
git commit -m "Include Resume exclusions in application snapshots"
```

### Task 4: Create, Upload, Edit/Save, and Master Resume UI

**Files:**
- Modify: `backend/app/main.py`
- Modify: `frontend/app/resumeReview.ts`
- Modify: `frontend/app/resumeReview.test.mjs`
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/app/globals.css` only if existing warning/card styles cannot cover the controls
- Test: `backend/tests/test_real_user_regression.py`
- Test: `frontend/app/resumeReview.test.mjs`
- Test: `frontend/app/betaOperations.test.mjs`

**Interfaces:**
- Consumes: Task 1 reconciliation and Task 2 API response contract.
- Produces: `excludeExperience(candidateId)` and `restoreExperience(candidateId)` UI actions.
- Produces: `addCandidateAsExperience(issue)` draft creation using exact candidate anchors.

- [ ] **Step 1: Write failing backend high-frequency-path tests**

For create, upload, and edit/save, assert exact matching exclusions survive responsibility/source prose changes, any anchor change drops the old exclusion and saves with the new unresolved candidate, and adding a structured experience removes the stale exclusion. Assert parsed-entry `needs_review` still returns 409 and leaves persisted Resume data unchanged. Assert successful responses expose `unresolved_experience_count` and current review candidates.

- [ ] **Step 2: Run backend path tests and record RED**

Run: `cd backend; .venv/Scripts/python.exe -m pytest tests/test_real_user_regression.py -k "create_with_exclusion or upload_with_exclusion or edit_with_exclusion" -q`

Expected: FAIL because these paths do not reconcile or persist exclusion state.

- [ ] **Step 3: Reconcile exclusions in create/upload/edit**

Call the Task 1 reconciliation function after final source/experience normalization and before integrity checking. Upload preserves only exact current IDs. Create starts with `[]`; edit uses the stored exclusion JSON. Split save validation so parsed entries with `needs_review` still block, while uncovered candidates persist and are returned with `unresolved_experience_count` plus structured review candidates.

- [ ] **Step 4: Write failing frontend state and interaction tests**

Extend `resumeReview.test.mjs` to assert unresolved/excluded partitioning, Add creates a draft with exact anchors, and failed exclusion/restore helpers return existing experiences unchanged. Extend `betaOperations.test.mjs` to require accessible labels `Add as work experience`, `Do not use this experience`, `Excluded experiences (N)`, and `Restore`, plus the PATCH URL and actions.

- [ ] **Step 5: Run frontend tests and record RED**

Run: `cd frontend; npm test -- --runInBand`

Expected: FAIL on missing helpers, controls, and endpoint call.

- [ ] **Step 6: Implement the minimum UI**

Extend `ResumeReviewIssue` with `candidate_id` and `status`. Render unresolved candidates with Add/Do-not-use buttons. Render excluded candidates in a closed `<details>` section with Restore. Use the existing authenticated fetch, notice, refresh, and warning styles. `Add` appends a draft card populated only with candidate anchors and scrolls/focuses the editor; it does not invent responsibilities. API failures update notices but never call `setExperiences` with server data, preserving the user's draft. After create/upload/edit succeeds with unresolved candidates, show `Saved. N work experiences still need your decision before documents can be generated.` instead of the ordinary success message.

- [ ] **Step 7: Run Task 4 backend and frontend tests GREEN**

Run: `cd backend; .venv/Scripts/python.exe -m pytest tests/test_real_user_regression.py -k "create_with_exclusion or upload_with_exclusion or edit_with_exclusion" -q`

Run: `cd frontend; npm test -- --runInBand`

Expected: both PASS.

- [ ] **Step 8: Commit Task 4**

```powershell
git add backend/app/main.py backend/tests/test_real_user_regression.py frontend/app/page.tsx frontend/app/resumeReview.ts frontend/app/resumeReview.test.mjs frontend/app/betaOperations.test.mjs frontend/app/globals.css
git commit -m "Add reversible Resume experience exclusion controls"
```

### Task 5: Real Production Fixtures, Full Verification, and Rollout Gate

**Files:**
- Modify: `backend/tests/test_real_user_regression.py`
- Modify: `frontend/app/resumeReview.test.mjs` only if production fixture response shape reveals a missing assertion
- No production code unless a new test first demonstrates a defect

**Interfaces:**
- Consumes: all Tasks 1-4 behavior.
- Produces: regression evidence for the three confirmed production candidates and a deployable branch.

- [ ] **Step 1: Add the three-real-candidate end-to-end regression**

Use `PRODUCTION_MISSING_EXPERIENCES_SOURCE` and the corrected nine-experience fixture. Discover and exclude exactly:

```text
Sodex | Utility | December 2023 - April 2024
Puma, Port Hedland | service station work | March 2023 - May 2023
Self-employed - Amazon e-commerce business | Self-employed e-commerce operator | 2019 - 2022
```

Assert the three IDs are distinct and stable; risk scan has no unresolved candidate; Department, Mable, My Support, all other seven experience facts, and the complete CKB JSON remain unchanged; Update to latest succeeds only after exclusions enter the snapshot; mocked Generate reaches document generation rather than a 409 integrity guard.

Add a separate deadlock regression: upload a brand-new Resume with one uncovered candidate; assert upload returns success and an unresolved count instead of 409; exclude the server-returned candidate; assert risk scan has no unresolved entries; create/update an application snapshot and assert Update to latest plus mocked Generate pass the integrity gates.

- [ ] **Step 2: Run the real regression RED or GREEN for the right reason**

Run: `cd backend; .venv/Scripts/python.exe -m pytest tests/test_real_user_regression.py -k "production_exclusions" -q`

Expected: PASS if Tasks 1-4 fully cover the production fixture. If it fails, use systematic debugging, add the smallest fix only after the failing assertion identifies the defect, and rerun.

- [ ] **Step 3: Run complete backend verification**

Run: `cd backend; $env:AI_PROVIDER='openai'; .venv/Scripts/python.exe -m pytest -q`

Expected: all backend tests pass with zero failures.

- [ ] **Step 4: Run complete frontend verification and build**

Run: `cd frontend; npm test`

Run: `cd frontend; npm run build`

Expected: all frontend tests pass and production build exits 0.

- [ ] **Step 5: Review the full diff and commit verification fixtures**

```powershell
git diff --check
git status --short
git add backend/tests/test_real_user_regression.py frontend/app/resumeReview.test.mjs
git commit -m "Cover production Resume exclusions across all integrity gates"
```

- [ ] **Step 6: Deployment and production data gate**

Push only after the full-branch review is clean and explicit integration approval is present. Confirm the deployed commit via Render before changing production data.

Call the new PATCH endpoint exactly once per current server-derived candidate ID for Resume id 2. Archive before/after account-data exports. Verify only `experience_exclusions_json` and Resume `updated_at` changed; source, experiences, and CKB hashes must match.

- [ ] **Step 7: Curtin production verification**

Confirm application id 41 first reports an outdated snapshot. Click Update to latest once, verify its snapshot now contains all three exclusions, then run Generate once. Confirm Tailored CV is created and no Master Resume integrity 409 occurs. Do not change the three excluded experiences or other applications.
