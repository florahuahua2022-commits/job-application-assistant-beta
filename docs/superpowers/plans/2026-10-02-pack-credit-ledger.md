# Pack Credit Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace beta generation quotas and the separate Selection Criteria allowance with lifetime Pack credits, atomic reservation, manual top-ups, and an independent global monthly safety cap.

**Architecture:** An immutable Pack credit ledger is paired with a materialized per-user balance for atomic debits. `generationusage` tracks one reservation per user and Pack, while a locked monthly counter enforces the global 500-Pack cap. Payment collection is explicitly excluded; the package catalog only validates audited manual top-ups.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, PostgreSQL/Supabase, SQLite tests, Next.js.

**Spec:** Confirmed in the 2026-10-02 product conversation and `docs/COMMERCIAL_RELEASE_PLAN.md`.

## Global Constraints

- New and historical users receive exactly 2 lifetime Pack credits; historical generation is not charged retroactively.
- A CV + Cover Letter Pack costs 1 credit; a Pack including Selection Criteria costs 2 credits.
- `DAILY_PACK_LIMIT_PER_USER` and the separate Selection Criteria allowance are retired.
- The global monthly safety cap is 500 Packs and remains separate from personal balances.
- No payment SDK, checkout, webhook, automatic payment confirmation, refund workflow, or purchasing UI is in scope.
- `single` (1/A$16.95), `starter` (8/A$109.95), and `job_search` (18/A$199.00) are validation metadata for manual ledger grants only.
- Complete and report each of the three commits separately; do not push.

## Review Focus

- Concurrent requests for a user's last credit must not make the balance negative.
- Concurrent requests for the 500th global slot must not admit a 501st Pack.
- Repeated document calls, retries, releases, and free grants must be idempotent.
- Failure or expiry must restore both personal credits and the global reservation exactly once.
- Existing Selection Criteria ledger entries must never affect Pack credit balances.

---

### Task 1: Data model and migration

**Files:**
- Create: `supabase/migrations/20261002_pack_credit_ledger.sql`
- Create: `backend/app/pack_credits.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/database.py`
- Modify: `backend/tests/test_online_security.py`

- [ ] Write failing tests for model fields, package catalog values, migration constraints/RLS, and historical `generationusage` status mapping.
- [ ] Run the focused tests and confirm failure because the new schema and catalog do not exist.
- [ ] Add `PackCreditAccount`, immutable `PackCreditLedger`, `GlobalMonthlyUsage`, and reservation fields on `GenerationUsage`.
- [ ] Add the three-package validation catalog without any payment integration.
- [ ] Add Supabase and local upgrade migrations; map completed history to `completed` and incomplete history to `released`.
- [ ] Run focused and full backend tests, review the diff, and commit only Task 1.
- [ ] Stop for user review.

### Task 2: Atomic credit engine

**Files:**
- Modify: `backend/app/pack_credits.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/auth.py`
- Modify: `backend/tests/test_online_security.py`

- [ ] Write failing tests for free grants, costs, idempotency, concurrent personal/global reservations, completion, release, expiry, and manual top-up validation.
- [ ] Implement transactional reserve/complete/release operations and the protected manual top-up API.
- [ ] Run focused and full backend tests, commit only Task 2, and stop for user review.

### Task 3: Integrate generation flow

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Modify: `backend/.env.online.example`
- Modify: `frontend/app/page.tsx`
- Modify: relevant backend/frontend tests

- [ ] Write failing tests proving the Pack cost is reserved once, failures release it, old Selection Criteria credits are ignored, and daily limits are absent.
- [ ] Replace quota and Selection Criteria checks with the Pack credit engine; remove obsolete UI/configuration.
- [ ] Run all backend tests, all frontend tests, and the production frontend build.
- [ ] Commit only Task 3 and stop for user review; do not push.
