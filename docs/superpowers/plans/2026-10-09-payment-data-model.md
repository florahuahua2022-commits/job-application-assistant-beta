# Payment Data Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver payment persistence in two backward-compatible commits: 2a adds orders/events and atomic idempotent grants; 2b adds lots, allocation, history backfill, reservation provenance, and refund storage.

**Architecture:** Reuse `purchase`. Commit 2a leaves existing balance/reservation semantics intact and introduces a compatibility `get_available_pack_credits()` function before any later balance change. Commit 2b builds FIFO provenance behind that stable read interface. Both migrations are idempotent, transactional, RLS-protected, and tested on GitHub Actions PostgreSQL 16.

**Tech Stack:** Python 3.12, SQLModel/SQLAlchemy, PostgreSQL 16, psycopg 3, Supabase SQL migrations/CLI 2.100.1, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-09-credit-payments-schema-and-data-model-design.md`

## Global Constraints

- No production access, real Stripe calls, keys, Checkout/Webhook/refund HTTP routes, or frontend work.
- Write and observe each commit's PostgreSQL tests failing before production code.
- New objects are created only by `2026100901_payment_orders_events.sql` and `2026100902_credit_lots_refunds.sql`.
- Every new/extended payment table enables RLS and revokes all privileges from `public`, `anon`, and `authenticated`.
- Every backend function revokes execution from `public`, `anon`, and `authenticated`; grant only to the confirmed production backend database role and CI `service_role`.
- Production deployment order is mandatory: backup and preflight → execute production migration → verify → push `main`. Never push code that expects unapplied schema.
- Production `create_all` remains temporarily, but excludes `purchase`, `stripeevent`, `packcreditlot`, `packcreditallocation`, and `paymentrefund`; tests may explicitly create them.
- The later removal of all production startup DDL remains a separate commit.

## Production Role Gate

The repository cannot infer the username inside Render's production `DATABASE_URL`. Before production migration, the owner records only the username/role (never the password or URL) from the Render connection configuration or `select current_user`. Put that exact role into the reviewed migration grant statement. After migration, run `has_function_privilege('<role>', '<signature>', 'EXECUTE')`; application startup also performs this read-only check and refuses readiness if false.

## Production Backup and Execution Gate

Before each migration, create a restorable custom-format backup limited to `public.packcreditaccount`, `public.packcreditledger`, `public.generationusage`, and `public.purchase`. Use `pg_dump --format=custom --table=...` from the owner's secure environment, store it privately, and verify it with `pg_restore --list`. Do not commit the backup.

Each migration uses one transaction and takes locks in this order before backfill or constraint replacement: account advisory lock namespace, `packcreditaccount`, `generationusage`, `purchase`, `packcreditledger`. Use the weakest lock that prevents concurrent writes; 2b uses explicit table locks during historical replay. Any error rolls back the whole migration. Do not retry blindly after an unknown disconnect; first rerun the read-only preflight.

## Read-Only Preflight

Create `supabase/diagnostics/payment_preflight.sql`, containing only SELECT/CTE queries for:

1. Per-account comparison of `packcreditaccount.balance` with `sum(packcreditledger.credits_delta)`.
2. `release` rows with no debit for the same `(user_id, pack_id)`, multiple candidate debits, duplicate releases, or an amount unequal to the debit.
3. Existing `purchase` row count, distinct status/currency values, null/duplicate Stripe IDs, and min/max timestamps/amounts without returning user data.
4. Current database role and its future function privileges.

Any mismatch or unknown purchase status blocks production migration and is resolved by an explicit migration decision, never an automatic guess.

## Backward-Compatible Balance Contract

All production balance decisions route through `get_available_pack_credits(user_id)` after 2a. In 2a it returns the existing `packcreditaccount.balance`. In 2b it returns total unsettled balance minus active reservations. `pack_credit_balance`, reservation return values, insufficient-credit checks, and API responses use this function; backups/diagnostics may read raw balance but cannot make authorization decisions.

The 2a code is therefore safe after the 2b migration but before the 2b code push. A repository-wide search must confirm no other production decision reads `PackCreditAccount.balance` directly.

## Review Focus

- Missing revoke statements must fail CI because test roles inherit Supabase-like default privileges.
- A failed event must be persisted after the failed business transaction rolls back and remain retryable.
- Event replay with different immutable facts must fail without a second grant.
- 2b must backfill manual/free grants and Stripe grants created during the 2a deployment window.
- Old 2a application code must report spendable balance correctly after the 2b migration.

---

## Commit 2a Payment Orders Events and Atomic Grants

### Task 2a.1 Write and observe failing PostgreSQL tests

**Files:** Create `backend/tests/postgres_credit_database.py`, `backend/tests/test_payment_orders.py`, and `backend/tests/sql/bootstrap_supabase_test.sql`; modify `backend/tests/test_pack_credits.py`.

- [ ] Bootstrap `anon`, `authenticated`, and `service_role` plus Supabase-style default table/sequence/function privileges. Default grants intentionally expose new objects until migrations revoke them, so a missing revoke makes tests fail.
- [ ] Add RED tests for extended `purchase`, `stripeevent`, RLS/no frontend privileges, backend-only execution, and absence of duplicate `stripepaymentorder`.
- [ ] Add RED tests for concurrent duplicate event delivery, changed-fact replay rejection, processed no-op, and atomic rollback.
- [ ] Add `test_failed_business_transaction_is_recorded_failed_and_can_retry`: roll back a simulated processing transaction, call the separate failure recorder with the same immutable event facts, assert `failed`, then retry successfully and assert one grant.
- [ ] Add a test proving production `create_db_and_tables()` excludes all migration-only payment table metadata when `deployment_mode=online`.
- [ ] Run focused tests against PostgreSQL 16 and confirm behavioral RED failures, not import/setup errors.

### Task 2a.2 Add preflight and the idempotent orders/events migration

**Files:** Create `supabase/diagnostics/payment_preflight.sql` and `supabase/migrations/2026100901_payment_orders_events.sql`; modify `backend/app/models.py`.

**Interfaces:** Produce SQLModel `Purchase` and `StripeEvent`; PostgreSQL `get_available_pack_credits(uuid)`, `grant_pack_credits(...)`, preserved `grant_manual_pack_topup(...)`, `process_stripe_purchase_event(...)`, and `record_stripe_event_failure(...)`.

- [ ] Write the SELECT-only preflight queries listed above.
- [ ] Extend `purchase` idempotently with package, cents/currency snapshots, `single_pack_price_cents`, actual fee IDs/amount, paid timestamp, and dispute status. Replace constraints only after validating existing values.
- [ ] Create `stripeevent` idempotently. `processed` is the only terminal dedupe state; `failed` may move back to processing.
- [ ] Generalize the existing grant algorithm without lots. Manual grants keep their wrapper/API; Stripe grants add a purchase-linked ledger row and use the same balance/idempotency implementation.
- [ ] Implement failure persistence as a second transaction boundary: `process_stripe_purchase_event` never catches and commits a partial failure; its caller rolls back, then calls `record_stripe_event_failure` with the immutable event/order fingerprint. That function upserts `failed`, increments attempts, and rejects a mismatched fingerprint. A later retry locks that row and changes it to `processing`.
- [ ] Enable RLS and explicitly revoke tables, sequences, and functions. Grant execution to CI `service_role` and the owner-confirmed production role.
- [ ] Add matching SQLModel declarations; do not add lot/refund models.

### Task 2a.3 Protect startup and all balance reads

**Files:** Modify `backend/app/database.py`, `backend/app/pack_credits.py`, and `backend/tests/test_online_security.py`.

- [ ] In online/PostgreSQL mode, call `create_all` only with metadata tables whose names are not migration-only payment tables. Local SQLite test setup remains explicit.
- [ ] Route `pack_credit_balance` and PostgreSQL reservation responses/checks through `get_available_pack_credits`; 2a behavior remains numerically unchanged.
- [ ] Search all balance reads. Tests and backup serialization may remain; production authorization/API decisions must use the compatibility function.
- [ ] Add startup read-only checks for required payment tables, function signatures, and confirmed backend-role EXECUTE privilege; missing objects/privilege make readiness fail without DDL.

### Task 2a.4 Add CI dry-run and PostgreSQL 16 job

**Files:** Modify `.github/workflows/quality-gates.yml`.

- [ ] Add a separate `postgres:16` service job with database `job_assistant_test` and no production secrets.
- [ ] Install Supabase CLI 2.100.1.
- [ ] Run `supabase db push --db-url "$PACK_CREDIT_TEST_DATABASE_URL" --dry-run --include-all` before applying migrations; upload output.
- [ ] Apply migrations to the isolated service, run 2a tests with no PostgreSQL skips, and upload results.
- [ ] Run the existing SQLite/full backend suite and frontend tests/build.

### Task 2a.5 Verify commit and deploy

- [ ] Verify RED/GREEN evidence, `git diff --check`, no secrets, no lots/refunds, and no direct production balance decision reads.
- [ ] Commit: `git commit -m "Add payment orders and idempotent event grants"`.
- [ ] Production: confirm DB role → run preflight → create/verify backup → execute migration transactionally → verify schema/permissions → only then push `main` and deploy 2a.

---

## Commit 2b Credit Lots Allocations and Refund Storage

### Task 2b.1 Write and observe failing lot/refund tests

**Files:** Create `backend/tests/test_credit_lots.py`; modify `backend/tests/test_pack_credits.py`.

- [ ] Add RED tests for FIFO spanning lots, exact-source release, admin/free non-refundability, account invariant, and refusal to restore a refunded lot.
- [ ] Add RED migration fixtures containing pre-2a manual/free history and 2a-period `grant_stripe_purchase` rows; all must become lots.
- [ ] Add RED tests for missing/ambiguous/duplicate historical releases blocking migration.
- [ ] Add RED tests for refund table checks, order/idempotency uniqueness, statuses, lease/indexes, RLS, and no frontend privileges. Do not implement refund orchestration or Stripe calls.
- [ ] Add a compatibility test: apply 2b migration, run the 2a application balance path, create an active reservation, and assert it returns spendable—not raw total—balance.
- [ ] Run against PostgreSQL 16 and observe expected RED failures.

### Task 2b.2 Add lots refund storage and historical backfill

**Files:** Create `supabase/migrations/2026100902_credit_lots_refunds.sql`; modify `backend/app/models.py`.

- [ ] Create `packcreditlot`, `packcreditallocation`, and `paymentrefund` idempotently; enable RLS and revoke default/public/frontend privileges.
- [ ] Lock writes in the documented order, then replay ledger by `(created_at, id)`: free/manual/2a Stripe grants create lots; debits allocate FIFO; releases reverse the unique matching debit. Abort on ambiguity.
- [ ] Convert raw account balance to total unsettled balance by adding active reservations once, record a migration marker, and verify per account: `total balance = remaining lots + active reservations`.
- [ ] Replace `get_available_pack_credits` so it subtracts active reservations. This keeps the running 2a application correct during migration-first deployment.
- [ ] Add refund storage fields/indexes for 30-day state lookup, processing leases, integer snapshots, deterministic Stripe idempotency, and dispute linkage. Do not add refund execution functions.
- [ ] Add matching SQLModel declarations.

### Task 2b.3 Make reservation functions allocation-aware

**Files:** Modify `supabase/migrations/2026100902_credit_lots_refunds.sql`, `backend/app/pack_credits.py`, and `backend/tests/test_pack_credits.py`.

- [ ] Replace PostgreSQL reserve/complete/release functions under the global lock order. Reserve reduces lot availability and records allocations but not total balance; completion decreases total balance; release restores exact lots and not total balance.
- [ ] Preserve existing signatures and return spendable balance through `get_available_pack_credits`.
- [ ] Mirror observable behavior in SQLite for fast unit tests; concurrency claims remain PostgreSQL-only.
- [ ] Reject release to a refunded purchase lot and emit a consistency error rather than minting credits.

### Task 2b.4 Verify commit and deploy

- [ ] Run Supabase dry-run first, apply both migrations to fresh PostgreSQL 16, and run all payment/credit tests with no skips.
- [ ] Run full backend/frontend verification, invariant/permission tests, and `git diff --check`.
- [ ] Commit: `git commit -m "Add FIFO credit lots and refund storage"`.
- [ ] Production: rerun preflight → create/verify backup → pause generation writes → execute migration transactionally with backfill locks → verify invariants/permissions/2a compatibility → only then push `main` and deploy 2b.

## Deferred to the Refund Implementation

The “first refund blocked in Stripe while a second submits,” account 30-day lock, processing lease recovery, fee-unavailable reconciliation, zero-amount no-call, dispute rejection, generation-reservation conflict, and Stripe failure compensation tests remain mandatory for the later refund orchestration commit. This plan creates their storage contracts only.
