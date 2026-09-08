# PRD v2.1 delivery quality update

Updated 2026-09-08 against the supplied Job Application Assistant Beta PRD v2.1. This records the implemented update and the remaining acceptance work, not a claim of full PRD acceptance.

## Architecture audit before edits

The current application generates a saved `GeneratedDocument.content` through one document-type-specific generation and review flow. Single-file export and pack export both pass that saved content to `create_docx` or `create_pdf`. Export does not call a generation provider. The saved structured plan, source snapshot, review and input fingerprint accompany the content. The existing Evidence Plan, bounded repair loop, resume timeline curation, styles, source acquisition and release checklist were retained.

This rules out separate DOCX/PDF generation in the current checkout. It does not establish how the historical PDF samples were produced or whether they used the same input/version as Word v153–155. No speculative generation-architecture rewrite was made.

## Changes

- Numbered criteria now retain wrapped continuation lines, number, category and text offsets. Paste and JDF extraction share this parser. Essential/Desirable headings and application instructions are excluded. Attachment offsets point into the extracted source. Unnumbered or unusual source layouts still need confirmation through the existing requirement-review flow.
- Applicant name, email and phone are checked on Resume, Cover Letter and Selection Criteria. Empty profile fields prevent confirmation and finalisation. The confirmation card explicitly displays the name to use across documents, the override rule for older/preferred names, work rights and availability. Changes to preferred name or work rights invalidate the saved confirmation and pack review. The existing confirmation gate continues to prevent unconfirmed packs from becoming Ready.
- Selection Criteria receives the same profile contact header. Criteria with no matched evidence return `insufficient_evidence` and affected criterion IDs before generation, rather than producing a refusal paragraph.
- Aggregate tenure uses the fixed `employment_calendar_complete_months_v1` rule: employment evidence only; part-time contributes calendar tenure, not FTE; project, volunteer and education records are excluded; overlapping intervals are merged; gaps are not counted. Only fully established interior months count. Year-only, missing, invalid or future periods suppress the aggregate claim. The allowed wording, included evidence, exclusions, intervals, date and computed total are recorded in the generation trace. Related-experience and FTE claims remain blocked without a separate approved scope; no model-inferred scope is accepted.
- Shared hard checks run after generation, on edited-document re-review and in Final Check. They flag unsupported aggregate claims, missing or conflicting canonical identity, contradictory work rights, invalid answer placeholders and body text after a closing/signature. Availability now recognises “will be available”, including the dated promise in the historical letter.
- Literal finance-duty attribution checks detect journals, reconciliation and Dayforce moving to a role whose own source does not support them. Semantic review remains necessary for other duties and paraphrases. Resume skills outside employment entries are not attributed to the final role.
- Both export paths reject placeholder/signature structural defects in addition to existing text-preservation and order checks. PDF extraction records truly blank pages. Draft filenames remain explicit; a successful draft download is not release approval. Prompt trace version is now 3.1.

## Verification

Final result: 446 backend tests passed; 28 frontend tests passed; production frontend build passed; `git diff --check` passed. Two existing re-review fixtures were completed with canonical contact details to satisfy the new identity contract.

Backend checks cover five wrapped essential criteria with a separate desirable section; overlapping employment and excluded categories; uncertain dates; canonical identity across all document types; changed confirmation values; Mable/My Support finance contamination; availability conflicts; and both DOCX/PDF exports of valid and invalid content. Existing generation, source acquisition, curation, repair, security and release tests also run.

Historical files from Downloads were inspected without modifying them. The Word Selection Criteria v155 placeholder and the PDF Cover Letter's post-signature paragraphs are detected. Re-rendering their extracted content through each current exporter fails the corresponding structural checks. The Word CV v153 aggregate tenure claim is flagged. These checks establish rejection of known bad content, not quality of a newly generated real application. Private extracted text and inspection results are retained under ignored `tmp/`, including `prd-v21-real-samples.json`; no applicant documents were added to fixtures.

Validation commands (from the respective backend/frontend directories):

```text
AI_PROVIDER=openai python -W ignore -m unittest discover -s tests -q
node --experimental-strip-types --test app/*.test.mjs
node node_modules/next/dist/bin/next build
```

Provider selection in the test command makes the existing mocked-provider tests deterministic; it does not make paid generation calls. Frontend verification uses the bundled Node 24 runtime.

## Remaining acceptance work

- The original MHC JDF and the exact matching Master Resume/source snapshot were not located in the local application data. A five-item synthetic regression does not establish the original JDF's exact five-item acceptance. Supply/reconnect those inputs for a fresh end-to-end run.
- General PDF geometry, clipping, orphan-heading and DOCX visual pagination checks are not established by text extraction. Existing renderers and styling are retained; no new visual quality certification is claimed.
- Aggregate recognition and responsibility attribution use bounded English rules, supplemented by the existing semantic reviewer. A configurable relevant/FTE tenure scope, explicit calendar availability editor, and comprehensive arbitrary identity-alias handling are not implemented in this update.
- Career Ops paired-input blind review, repeated live generation, 90% first-pass delivery quality, and zero P0 errors over the complete real fixed dataset remain unmeasured. Those targets cannot be inferred from unit-test success.
- Changes are local. No deployment, automatic application submission or rewrite of saved user drafts was performed.
