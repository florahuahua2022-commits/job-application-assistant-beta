# Master Resume experience exclusions

## Goal

Allow a user to distinguish an accidentally omitted work experience from one they deliberately do not want used in generated application materials. A deliberate exclusion is persistent, reversible, visible, auditable, and does not weaken the existing checks for unresolved omissions.

## Scope

This change covers Master Resume creation, upload, editing, risk scan, page-load review, application `Update to latest`, diagnosis/generation integrity checks, and application snapshots. It does not add application-specific experience selection; exclusions apply to the Master Resume until restored.

## Data model

Add `Resume.experience_exclusions_json`, defaulting to `[]`. Each entry contains:

```json
{
  "candidate_id": "stable deterministic id",
  "organization": "Sodex",
  "role_title": "Utility",
  "time_period_text": "December 2023 - April 2024",
  "source_excerpt": "Sodex: Utility, December 2023 - April 2024.",
  "status": "excluded_by_user",
  "excluded_at": "ISO-8601 timestamp"
}
```

`candidate_id` is derived only from the normalized `organization`, `role_title`, and `time_period_text` anchors. Normalization is the same exact case/punctuation/whitespace normalization already used for experience coverage. No semantic similarity or fuzzy matching is permitted.

If all three anchors remain unchanged, responsibility/source wording changes do not invalidate the exclusion. If any anchor changes, the changed candidate receives a different ID, the old exclusion is removed during reconciliation, and the new candidate is unresolved until the user decides again.

## Candidate states

`find_uncovered_experience_candidates` discovers source candidates and classifies each as:

- `unresolved`: absent from `experiences_json` and not matched by a current exclusion; blocks integrity checks.
- `excluded_by_user`: absent from `experiences_json` and matched by candidate ID; does not block.
- Covered candidates are represented by `experiences_json` and are not returned as uncovered candidates.

The detector continues to use deterministic string rules. It makes no LLM calls.

Stale exclusions that no longer match a current uncovered candidate are removed whenever the Resume is created, uploaded, edited, scanned, or an exclusion action is applied. Adding a candidate as a structured experience therefore clears its matching exclusion automatically.

## API

Add:

```http
PATCH /resumes/{resume_id}/experience-exclusions
```

Body:

```json
{"candidate_id": "...", "action": "exclude"}
```

or:

```json
{"candidate_id": "...", "action": "restore"}
```

For `exclude`, the server regenerates current candidates and rejects an ID that is not currently unresolved. It writes the server-derived anchors and excerpt rather than trusting client-provided facts. Repeating the same action is idempotent. For `restore`, the server removes the matching exclusion; an unknown ID is also an idempotent no-op. A successful change updates `updated_at` and invalidates evidence matches but does not modify `source_text`, `experiences_json`, or CKB facts.

`/resumes/risk-scan` returns unresolved and excluded candidates with their state. Only unresolved candidates contribute to `needs_review_count` and structured 409 details.

## Shared integrity behavior

All integrity callers use the Resume's reconciled exclusions:

1. Master Resume creation
2. Master Resume upload
3. Master Resume edit/save
4. `/resumes/risk-scan`
5. Master Resume page-load scan
6. Application `Update to latest`
7. Diagnosis and every generation preflight

Existing `needs_review` flags on parsed experiences remain blocking and cannot be dismissed through this mechanism.

## Application snapshots

`resume_snapshot_json` includes `experience_exclusions_json`. Snapshot freshness compares source text, structured experiences, and exclusions.

If an application snapshot was created before a candidate was excluded, excluding it later makes that snapshot stale. Diagnosis/generation retain the existing `Update to latest Master Resume` requirement. There is no special-case pass-through. After Update to latest, the snapshot contains the exclusion and the candidate no longer blocks.

Restoring an exclusion likewise changes the Master Resume version and makes older application snapshots stale.

## UI behavior

Each unresolved candidate appears in the existing Master Resume review area with:

- `Add as work experience`: creates a structured experience draft populated with the three anchors and removes the matching exclusion through normal reconciliation after save.
- `Do not use this experience`: calls the exclusion endpoint. Helper text explains that the source remains in the original Resume but generated applications will not use it.

Excluded candidates appear in a collapsed `Excluded experiences (N)` section with `Restore`. Restore returns the candidate to the unresolved list immediately.

Failed requests preserve all current form input and show the existing structured error style.

## Upload and editing rules

- Responsibility or surrounding prose changes with identical anchors preserve the exclusion.
- A change to organization, role title, or time period creates a new unresolved candidate and removes the old unmatched exclusion.
- Uploading another file preserves only exclusions whose candidate IDs still exactly match candidates in the uploaded source.
- Excluded candidates are not added to `experiences_json` or CKB and are unavailable to generated documents.

## Migration and production rollout

Add the new column for SQLite and PostgreSQL through the existing startup migration pattern. Existing Resume rows start with `[]` and therefore retain current blocking behavior until a user explicitly excludes a candidate.

After deployment, exclude the three confirmed candidates on production Resume id 2 through the new API: Sodex / Utility, Puma / service station work, and Self-employed - Amazon e-commerce business / Self-employed e-commerce operator. Preserve before/after snapshots and verify no source, experience, or CKB facts change.

Then confirm Curtin application id 41 is stale, run `Update to latest`, and generate one Tailored CV. Do not alter other candidate or application data.

## Acceptance tests

- Unresolved candidates block create, upload, edit/save, risk scan reporting, Update to latest, diagnosis, and generation.
- Excluded candidates remain discoverable/auditable but do not block any integrity caller.
- Repeated scan and repeated exclude calls are idempotent with stable order and no duplicate records.
- Restore makes the candidate unresolved and blocking again.
- Responsibility/source wording changes preserve an exclusion when all three anchors are unchanged.
- Changing any one anchor invalidates the old exclusion and creates a newly unresolved candidate.
- Adding the candidate to `experiences_json` removes its stale exclusion.
- Upload preserves exact-anchor exclusions and drops unmatched exclusions.
- Snapshots contain exclusions and freshness includes them.
- Excluding after an application snapshot exists makes that snapshot stale; Update to latest is required before diagnosis or generation.
- The existing duty-shaped-title and structured-field support checks are unchanged.
- Real fixtures for Sodex, Puma, and Amazon can all be excluded while Mable, My Support, Department of Communities, unrelated experiences, and CKB facts remain unchanged.
