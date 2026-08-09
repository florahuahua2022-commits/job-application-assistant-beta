# Release quality gates

The `Quality gates` GitHub workflow is the required build evidence for pull requests and `main`.

## Required green checks

- Backend compilation and the complete `unittest` suite.
- All six fixed Resume x JD pairings.
- Human-labelled Reviewer Pass and Fail regression cases.
- Production Next.js frontend build.
- No unexplained schema, grounding, limit or trace regression.

## Blocking release conditions

- Any open Critical defect.
- Any fabricated figure or unsupported material claim in an adjudicated fixture.
- Any missed fixed Critical Reviewer case.
- Any mandatory limit branch failure.
- Missing generation trace for a new document.
- Failed backend or frontend CI check.

## Rollback and feature-disable path

Set the affected Render environment variable to `false`, then redeploy:

- `ENABLE_TAILORED_RESUME`
- `ENABLE_COVER_LETTER`
- `ENABLE_SELECTION_CRITERIA`
- `ENABLE_ATS_ANALYSIS`

Disabling a generator blocks only new generations for that document type. Existing documents remain available for review and export. Re-enable only after the failing case has been added to the regression suite and all quality gates are green.

## Build evidence

Each workflow run retains backend test output and frontend build output as GitHub Actions artifacts for 30 days. The pull request and merge commit identify the exact source version used by the run.
