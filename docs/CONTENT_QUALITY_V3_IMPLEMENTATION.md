# Content Quality V3 implementation

Updated 2026-09-07. Implements the supplied product-content-quality-prd-v3.md in the existing local application. This is an engineering implementation and regression record, not a production quality certification or a Career Ops equivalence claim.

## Product changes

- Original role paragraphs, stable source groups, source locations where recoverable, date status and parsing confidence remain attached to split evidence. Year and ISO date ranges are accepted without treating dated budget duties as new jobs. Legacy evidence without the new provenance is refreshed when used.
- Resume source density is assessed from grouped role details. Short evidence ratios and crossing 451/455 words cannot independently block release. Wholly generic source duties request user detail; unused distinctive source facts and repeated generic case writing request system repair. The semantic reviewer checks paraphrases beyond the deterministic checks.
- Cover letters compare matched candidates against their three priorities, select up to three complementary role cases, preserve the selected role's matched facts and record candidate selection/exclusion reasons. Existing selection-criteria allocation does not exclude a stronger independent letter case.
- Job models retain explicitly labelled recruiter/employer candidates, their source spans, recruitment relationship excerpts and the selected display name. Unsupported short labels such as WA gov are not used in generated headings. Unresolved identities use a neutral greeting. Existing organisation editing and details confirmation remain available.
- Explicit advertisement skill-tag blocks enter the job model and matcher. Candidate mappings are retained with direct/adjacent/gap status in match results and the Resume Plan. Supplier coordination without contract evidence cannot become Contract Management. Equivalent skill entries are removed across Key Skills and Technical Skills; references-on-request wording is off by default.
- Profile adds immediate availability, retains not_specified as the default, validates allowed values, and displays the current selection before generation. Generated unsupported/conflicting availability is recorded and polished; reviewers and Final Check independently block unsupported edited or regenerated promises. Interview availability, reference availability and historical volunteer scheduling are not start-date promises.
- Generation trace includes the Profile snapshot and initial availability corrections; raw JD changes invalidate previous inputs. Factual and writing review outcomes remain separate, repair rounds retain versions, structural failures retain inspectable drafts, and edited text awaits a fresh review.
- Career Modern is an optional export style for both formats and documents. Switching style does not call generation or change the body. Export checks verify source-word preservation and order. Export trace records format, template token version, document/body/artifact fingerprints and check results.

## Frozen Career Modern tokens

The authoritative values live in `backend/app/career_modern.py`, token version `career_modern.1`. This implementation uses its own layout; no Career Ops template code was copied.

| Token | Value |
|---|---|
| Page | A4, single column |
| Margins | 0.7 inches |
| Font | Arial; PDF embeds local Arial when available, otherwise explicit Helvetica fallback at unchanged sizes |
| Name | 23 pt |
| Section and role | 11 pt |
| Body and organisation | 10.5 pt |
| Contact | 9 pt |
| Line spacing | 1.16 |
| Paragraph after | 5 pt |
| Section before | 12 pt |
| Bullet indent / hanging | 12 pt / 8 pt |
| Rule | 0.5 pt |
| Resume ink / section / organisation | 17243A / 087E8B / 6D4288 |
| Cover letter ink / metadata | 111111 / 596674 |

Role headings, employer metadata and dates stay with the first following bullet. Long employer names wrap without competing with the right-aligned date line. Empty sections are removed, but parent headings with populated subsections remain. The existing Classic default is unchanged.

## Verification and release limits

- Backend: all 438 tests pass, including V2 checks, timeline tests, employer whitelist/tense checks, version invalidation, edited drafts, export failures and V3 acceptance examples.
- Frontend: 26 tests and production build pass.
- Synthetic layout samples: two-page Resume and one-page Cover Letter PDFs rendered with Poppler and visually inspected. These are layout fixtures, not user career facts or a shared-input Career Ops benchmark.
- DOCX text, XML and A4 geometry are checked automatically. The packaged DOCX renderer was attempted but cannot run because this machine has neither LibreOffice nor Word. DOCX visual equivalence remains unverified.
- No live provider batch, six-input-set repeated generation, independent blind review, gold-fact annotation, latency/cost study or production deployment was performed. G0/G5 and PRD M10/section 16 therefore remain open. The new deterministic checks and prompts do not establish the PRD's statistical accuracy targets.
- Name and tag extraction is conservative and based on explicit labels/source spans; arbitrary unlabelled organisation mentions or unusual page layouts may still require user correction. The semantic reviewer remains necessary for paraphrases, responsibility boundaries, qualifiers and facts not covered by literal rules.

## Using the update

Start the application normally. Check Profile availability, then regenerate an application from the intended Resume/JD snapshot. Choose **Career Modern — Arial** in the export style selector and run application checks for that format/template. Old generated documents are not silently rewritten. Deployment and the outstanding release acceptance should follow a fixed set of shared inputs and human review.
