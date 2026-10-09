# Stripe Checkout and Webhook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add server-priced Stripe Checkout Session creation, signature-verified payment webhooks, operational reconciliation, and idempotent replay without connecting CI to Stripe or production.

**Architecture:** Keep FastAPI routes thin and place Stripe adaptation in `payments.py`; all successful credit grants continue through 2a `process_stripe_purchase_event`. Add migration-managed event/order operational fields and protected rate/idempotency/audit tables, then expose admin-only read/replay operations through `payment_operations.py`.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLAlchemy, stripe-python 16.0.0, PostgreSQL 16/Supabase CLI 2.100.1, unittest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-09-stripe-checkout-webhook-design.md`

## Global Constraints

- No production database access, real payment/refund, live Stripe request, or production secret.
- Pin Stripe API version `2026-09-30.endive` in code and test fixtures; do not inherit the account default.
- Checkout accepts only `package_code`; user ID and customer email come from the verified login session.
- All money is integer cents in AUD. `STRIPE_GST_ENABLED` defaults off: catalog subtotals are single `1695`, starter `10995`, job_search `19900`, GST is zero, and total equals subtotal. When enabled, calculate 10% GST once per whole order with decimal `ROUND_HALF_UP`; snapshot the matching tax-inclusive single total.
- Webhook body limit is 256 KiB and signature tolerance is 300 seconds.
- Only `processed` is successful terminal dedupe; `failed` remains auditable/replayable and `observed_pending` requires operations handling.
- Payment objects remain RLS-enabled with no `anon` or `authenticated` table/function privileges.
- CI uses GitHub Actions `postgres:16`, simulated Stripe objects/signatures, and no Stripe secret or network calls.
- Do not add frontend purchasing UI, refund execution, chargeback automation, or deployment steps. A minimal admin CLI for listing/reconciliation/replay is required before launch.

## Review Focus

- Concurrent sixth Checkout request: Task 3 tests that the durable 10-minute limit grants at most five slots.
- Same idempotency key with different packages: Task 3 tests `409` before a Stripe client call.
- Late async failure/expiry after payment: Task 5 tests that terminal paid orders cannot be downgraded.
- Test/live contamination: Tasks 4 and 5 test mode mismatch as deterministic `failed`, no grant, HTTP `200`, and admin visibility.
- Replay of a previously granted Session: Task 6 tests retrieval plus 2a reuse produces one ledger grant and a complete admin audit.
- Tax switch: Task 2 tests default-off totals `1695/10995/19900`, enabled totals `1865/12095/21890`, whole-order rounding, and the matching `single_pack_price_cents`.
- Stripe create failure: Task 3 proves the rate-limit slot is released only when the Stripe create call fails.

---

## File Structure

- `backend/app/payments.py`: catalog, Stripe client boundary, Checkout creation, signature verification, event normalization/classification.
- `backend/app/payment_operations.py`: failed-event queries, reconciliation report, Stripe refresh/replay, audit writing.
- `backend/app/pack_credits.py`: generalized 2a event-type/reason/livemode wrapper only; no Stripe SDK calls.
- `backend/app/main.py`: strict request/response models and thin authenticated/webhook/admin routes.
- `backend/app/auth.py`: authenticated user context containing verified ID and optional email.
- `backend/app/config.py`: Stripe mode/version/secrets and startup validation.
- `backend/app/models.py`: SQLModel declarations matching migration-only operational tables/columns.
- `supabase/migrations/2026100903_stripe_checkout_webhook.sql`: all schema, constraints, RLS, grants, and PostgreSQL functions.
- `supabase/diagnostics/stripe_checkout_preflight.sql`: read-only existing-row/mode/state/permission checks.
- `backend/tests/test_stripe_payments.py`: Checkout and Webhook unit/HTTP tests using fake Stripe.
- `backend/tests/test_payment_operations.py`: admin list, reconciliation, replay, and audit tests.
- `backend/tests/test_payment_orders.py`: PostgreSQL 16 transaction/idempotency/state tests.
- `backend/scripts/payment_admin.py`: minimal authenticated operator CLI for failed-event list, reconciliation, and replay.

---

### Task 1: Migration-managed Stripe operational schema

**Files:**
- Create: `supabase/migrations/2026100903_stripe_checkout_webhook.sql`
- Create: `supabase/diagnostics/stripe_checkout_preflight.sql`
- Modify: `backend/app/models.py`
- Modify: `backend/app/database.py`
- Test: `backend/tests/test_payment_orders.py`

**Interfaces:**
- Produces purchase fields `livemode`, `expires_at`, `refund_detected_at`, `checkout_idempotency_key_hash`.
- Produces stripeevent fields `livemode`, `failure_reason_code`, `stripe_object_id`, status `observed_pending`.
- Produces protected `paymentcheckoutrate` and `paymentoperationaudit` tables.

- [ ] **Step 1: Write failing schema tests**

Assert idempotent column/table creation, constraints/indexes, RLS, frontend revokes, service-role grants, unique `(user_id, checkout_idempotency_key_hash)`, event status/reason codes, and migration-only `create_all` exclusion.

- [ ] **Step 2: Run RED tests**

Run: `python -m unittest backend.tests.test_payment_orders -v`

Expected: FAIL because migration 0903 fields/tables do not exist.

- [ ] **Step 3: Add read-only preflight and transactional migration**

Preflight reports existing purchase/event row counts, null/ambiguous live mode, invalid states, duplicate intended idempotency keys, RLS, privileges, and confirmed backend role. Migration must abort rather than guess `livemode` for an existing row; on an empty pre-payment table it adds non-null live mode safely. Add `IF NOT EXISTS` where PostgreSQL supports it and replace checks/functions transactionally.

- [ ] **Step 4: Add matching SQLModel declarations**

Keep all new objects in `MIGRATION_ONLY_TABLES`; production `create_all` must not create them.

- [ ] **Step 5: Run schema tests on SQLite contract and PostgreSQL 16**

Run: `python -m unittest backend.tests.test_payment_orders -v`

Expected: local contract PASS; PostgreSQL tests PASS in CI with no skip.

- [ ] **Step 6: Commit**

Commit: `Add Stripe checkout operational schema`

---

### Task 2: Server catalog, authenticated identity, and Stripe boundary

**Files:**
- Create: `backend/app/payments.py`
- Modify: `backend/app/auth.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/pack_credits.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_stripe_payments.py`
- Test: `backend/tests/test_online_security.py`

**Interfaces:**
- Produces `AuthenticatedUser(id: UUID, email: str | None)` from verified claims.
- Produces `PaymentPackage` and `purchasable_package(package_code: str) -> PaymentPackage`.
- Produces `StripeGateway` methods for create/retrieve Event/Session; tests inject a fake.
- Produces `validate_stripe_settings(settings) -> None` and fixed `STRIPE_API_VERSION`.

- [ ] **Step 1: Write failing identity/catalog/config tests**

Assert exact GST-off and GST-on catalog values, whole-order half-up rounding, switch-aware `single_pack_price_cents`, `custom` rejection, email from verified claim only, stripe-python `==16.0.0`, fixed API version, and startup rejection for absent webhook secret in enabled online payment mode or mismatched test/live key prefix.

- [ ] **Step 2: Run RED tests**

Run: `python -m unittest backend.tests.test_stripe_payments backend.tests.test_online_security -v`

- [ ] **Step 3: Implement minimal catalog and Stripe gateway**

Initialize `StripeClient` with the explicit API version. Build the catalog from base subtotals and `stripe_gst_enabled=False`, using `Decimal`/`ROUND_HALF_UP` once per order when enabled. Keep imports/network calls behind the gateway; construction is allowed in production, calls occur only through route operations. Never log keys, Checkout URLs, raw webhook bodies, or email.

- [ ] **Step 4: Preserve existing auth callers**

Add a new authenticated-user dependency rather than changing every endpoint that currently consumes `UUID | None`; Checkout alone consumes the richer identity context.

- [ ] **Step 5: Run tests and compile**

Run: `python -m unittest backend.tests.test_stripe_payments backend.tests.test_online_security -v`

Run: `python -m compileall -q backend/app backend/tests`

- [ ] **Step 6: Commit**

Commit: `Add server Stripe payment boundary`

---

### Task 3: Checkout Session creation with durable idempotency and rate limit

**Files:**
- Modify: `backend/app/payments.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_stripe_payments.py`
- Test: `backend/tests/test_payment_orders.py`

**Interfaces:**
- Produces `POST /payments/checkout-sessions` with strict `{package_code}` request and `{checkout_session_id, checkout_url}` response.
- Produces PostgreSQL `reserve_checkout_creation(user_id, key_hash, package_code, now)` result: `reserved | existing | conflict | rate_limited`.

- [ ] **Step 1: Write failing HTTP and PostgreSQL concurrency tests**

Cover authentication, forbidden extra fields, exact price/GST/metadata/email/URLs/API version/30-minute expiry, explicit `payment_method_types=['card']`, same-key retry, different-package `409`, Stripe failure releasing its reserved rate slot, local persistence failure retaining it, and six concurrent unique keys yielding five reservations plus one `429`.

- [ ] **Step 2: Run RED tests**

Run: `python -m unittest backend.tests.test_stripe_payments backend.tests.test_payment_orders -v`

- [ ] **Step 3: Implement the database reservation function**

Use an account-scoped advisory transaction lock, unique idempotency row, and the 10-minute count before any Stripe call. Existing same-package keys return the stored Session; different-package keys return conflict. Add one small function to mark a reservation released after a failed Stripe create call; never release after Stripe returned a Session.

- [ ] **Step 4: Implement thin Checkout route**

Reserve the slot, call fake/real gateway outside the DB transaction, then persist `purchase.pending` snapshots, `expires_at`, `livemode`, and Session ID. A retry after Stripe success/local failure uses the same Stripe idempotency key and completes the local write.

- [ ] **Step 5: Run focused and PostgreSQL 16 tests**

Expected: no Stripe network call in tests and one remote Session per idempotency key.

- [ ] **Step 6: Commit**

Commit: `Add authenticated Checkout Session creation`

---

### Task 4: Signature-verified webhook and classified failure wrapper

**Files:**
- Modify: `backend/app/payments.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/pack_credits.py`
- Modify: `supabase/migrations/2026100903_stripe_checkout_webhook.sql`
- Test: `backend/tests/test_stripe_payments.py`
- Test: `backend/tests/test_payment_orders.py`

**Interfaces:**
- Produces `POST /payments/stripe/webhook`.
- Extends `process_stripe_purchase_event(..., stripe_event_type: str, livemode: bool)` while retaining Session-level grant idempotency.
- Extends `record_stripe_event_failure(..., reason_code: str, livemode: bool)`.
- Produces typed `TemporaryPaymentError` and `DeterministicPaymentConflict(reason_code)`.

- [ ] **Step 1: Write failing signature/body/failure tests**

Cover correct simulated signature; missing/expired/bad signature; declared and streamed 256 KiB overflow; raw-body preservation; temporary `failed + 500`; deterministic `failed + reason + 200`; duplicate failed retry; mode mismatch; and redacted logs.

- [ ] **Step 2: Run RED tests**

Run: `python -m unittest backend.tests.test_stripe_payments backend.tests.test_payment_orders -v`

- [ ] **Step 3: Generalize the 2a wrappers and SQL functions**

Event, order, and credit grant remain one transaction. On exception the Python wrapper rolls back, records failure in a second transaction, then raises the typed classification. Only temporary failures are rethrown to the route as `500`; deterministic conflicts are returned to the route after durable failure recording.

- [ ] **Step 4: Implement bounded body read, verification, and classification**

Verify before JSON use. Map only supported event names; compare Event/Session/order/config `livemode` and immutable order facts before invoking 2a.

- [ ] **Step 5: Run tests**

Expected: all simulated event tests PASS; no key or network dependency.

- [ ] **Step 6: Commit**

Commit: `Add verified Stripe payment webhooks`

---

### Task 5: Async failure, expiry, refund, and dispute observations

**Files:**
- Modify: `backend/app/payments.py`
- Modify: `supabase/migrations/2026100903_stripe_checkout_webhook.sql`
- Test: `backend/tests/test_stripe_payments.py`
- Test: `backend/tests/test_payment_orders.py`

**Interfaces:**
- Produces PostgreSQL `record_stripe_event_observation(...)` with `processed | observed_pending | conflict` result.
- Enforces automatic purchase transitions only from `pending`.

- [ ] **Step 1: Write failing event-state tests**

Cover completed paid/unpaid, async success/failure, expired pending, late failure/expiry after paid, refund observation, `charge.refunded` marker, dispute `needs_review/won/lost`, unmatched Stripe object, duplicate observation, and unknown-event no-write `200`.

- [ ] **Step 2: Run RED tests**

- [ ] **Step 3: Implement atomic observation/state function**

Payment failure and expiry are `processed` business outcomes. Refund/dispute events are `observed_pending`; they update only the approved detection/dispute fields and never balance, lots, or `paymentrefund`.

- [ ] **Step 4: Run focused and PostgreSQL concurrency tests**

Assert a late callback cannot downgrade a terminal order under concurrent delivery.

- [ ] **Step 5: Commit**

Commit: `Record Stripe payment lifecycle observations`

---

### Task 6: Admin failure list, reconciliation, and idempotent replay

**Files:**
- Create: `backend/app/payment_operations.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_payment_operations.py`
- Test: `backend/tests/test_payment_orders.py`

**Interfaces:**
- Produces `GET /admin/payments/failed-events`.
- Produces read-only `GET /admin/payments/reconciliation`.
- Produces `POST /admin/payments/replay` with exactly one event or Session ID.

- [ ] **Step 1: Write failing authorization/report/replay tests**

Assert admin-only access, pagination/filters, redaction, zero report writes, all specified discrepancy classes, exact-one replay target validation, Stripe re-retrieval, refund/dispute replay rejection, one grant under repeat/concurrency, and immutable audit rows.

- [ ] **Step 2: Run RED tests**

Run: `python -m unittest backend.tests.test_payment_operations backend.tests.test_payment_orders -v`

- [ ] **Step 3: Implement read-only queries**

Use joins/aggregates only; Stripe refresh is a separate explicit operation and never occurs while listing.

- [ ] **Step 4: Implement replay through 2a**

Retrieve the current Stripe test/live object with the fixed API version, rerun immutable fact checks, call `process_stripe_purchase_event`, and write admin ID, target, before/after status, result, reason, and time to the audit table. Event-ID replay must report Stripe retention expiry without fabricating an Event. Session-ID replay uses event ID `admin_replay:<checkout_session_id>` and event type `admin.replay`.

- [ ] **Step 5: Run tests**

- [ ] **Step 6: Commit**

Commit: `Add payment reconciliation and replay operations`

---

### Task 7: Branch-only verification and operating documentation

**Files:**
- Modify: `.github/workflows/quality-gates.yml`
- Create: `backend/scripts/payment_admin.py`
- Create: `docs/operations/stripe-payment-operations.md`
- Test: all backend/frontend/payment suites.

**Interfaces:** None; this task proves and documents the complete branch.

- [ ] **Step 1: Add CI assertions without secrets**

Run PostgreSQL 16 migrations with Supabase dry-run first, all payment tests with no PostgreSQL skips, simulated Stripe fixtures only, and a scan that rejects live keys/secrets/deploy steps.

- [ ] **Step 2: Add the minimal administrator CLI**

Provide list, reconciliation, and replay commands over the protected admin HTTP endpoints. Accept an operator-supplied base URL/token at runtime, never persist or print the token, and keep this as a stdlib client rather than adding a CLI dependency. This script is a production launch gate.

- [ ] **Step 3: Write Chinese operator runbook**

Document test-mode Stripe CLI forwarding, fixed API version, failed/observed queue review, reconciliation, safe replay, Event-ID retention limits, Session replay identity, refund/dispute manual steps, and secret cleanup. Recommend a restricted Stripe key with Checkout Session create/read plus Event, PaymentIntent, Charge, Refund, and Dispute read permissions; no Refund write. State that production migration/deploy/main merge require explicit owner approval.

- [ ] **Step 4: Run complete verification**

Run: `python -m unittest discover -s backend/tests -v`

Run: `python -m compileall -q backend/app backend/tests`

Run: `pnpm test && pnpm build` from `frontend`.

Expected: all local suites pass; GitHub PostgreSQL 16 job passes Supabase dry-run, permission, concurrency, and Stripe simulation tests; no deployment job runs.

- [ ] **Step 5: Inspect branch and secrets**

Run `git diff --check`, inspect every migration/function grant, confirm worktree clean after commit, and confirm Render remains main-only with previews off.

- [ ] **Step 6: Commit and push temporary branch only**

Commit: `Verify Stripe checkout and webhook operations`

Do not create/merge a PR or push `main` without explicit user approval.

---

## Production Gates (Not Authorized by This Plan)

Before any production action: confirm Supabase backup/PITR or complete CSV exports; obtain only the database connection username format to confirm backend function grants; run the read-only preflight; configure the matching Stripe Workbench webhook version and restricted key permissions; execute migration before application deployment; verify the administrator CLI can list failures, run reconciliation, and safely replay in test mode; then request explicit approval before merging or pushing `main`.
