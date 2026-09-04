# Content Quality V2 implementation record

Implemented against `product-content-quality-prd-v2.md` and the recovered 4 September 2026 Relevance / Timeline Integrity v1.1 specification.

## Open-source reference review

| Source | Fixed revision | Files reviewed | Decision | Licence / adaptation |
|---|---|---|---|---|
| `career-ops-hq/career-ops` (local checkout) | `5ad6133077089170b7d4bf388474c7bfd36d9fbc` | `modes/pdf.md`, `modes/cover.md`, `LICENSE` | Adapt concepts only; no source code copied | MIT. The local product keeps its own CKB, planner, reviewer, version and exporter pipeline. Career Ops' mandatory JD gate and interactive CLI confirmations were not copied because this product allows a draft from confirmed Resume and JD snapshots. |

The implementation keeps the existing product architecture. Installing or copying a Career Ops skill is not treated as integration evidence.

## Implemented behaviour

- CKB v2 retains separately written actions, including undated explicit role blocks, and keeps the original paragraph for provenance.
- Evidence matching and Resume planning have no default 10-record or three-record truncation. Explicit employer limits can still constrain output.
- Resume roles receive dynamic `core / adjacent / low` relevance and `full / condensed / timeline_only / hidden` display modes.
- Timeline checks use the generation date, a strict `> 12 months` fallback, a `<= 2 months` placeholder grouping threshold, exact constituent date ranges, and no repair of genuine gaps.
- Word count is guidance. Only thin source evidence among eligible relevant roles can produce `insufficient_source_detail`; otherwise a short CV is `concise_but_relevant`. Low and timeline-only roles cannot be expanded for length.
- Resume and Cover Letter reviewers check omitted supported detail as well as invented facts. Repairs keep every reviewed attempt and do not select a version with more severe findings.
- Cover Letter planning uses its own selected cases and the existing employer whitelist and employment-date/verb checks.
- Manual edits create a new document version and invalidate review. Application Resume updates are isolated to that application and preserve older documents.
- Generation requests with the same application, document type and pack ID are idempotent. Input, draft, actual provider/model, fallback, plan, review and final output remain traceable.
- DOCX and PDF exports verify repeated token retention and internal-marker removal for every selected document. Draft filenames identify their document version.

## Verification boundary

Automated tests cover deterministic acceptance logic, API isolation/versioning, duplicate requests, repair regression selection, both export formats and the existing product regressions. Real provider benchmarking, the six-scenario three-run comparison, visual review of real DOCX/PDF files, human blind scoring, cost/latency thresholds and production deployment remain G0/G4 release activities because this checkout has no configured AI credentials or confirmed production access. Those activities must complete before claiming Career Ops parity or production readiness.
