# Private beta deployed acceptance checklist

Repository tests do not make the deployment beta-ready. Record the deployed frontend/backend versions, Supabase project, tester, date and result for every item below. Do not paste Resume, JD, contact, token or API-key content into the evidence.

## Authentication

- [ ] Invite User A and User B through Supabase.
- [ ] Both invitation links establish a session and allow password creation.
- [ ] Sign out and sign back in with both accounts.
- [ ] Used, expired and malformed links show useful failure guidance.
- [ ] Supabase public email signup is disabled; an uninvited address is rejected.

## Isolation

- [ ] Each frontend shows only its owner's profile, Resume, applications and documents.
- [ ] Swapped application/document/source IDs return 404 through the backend.
- [ ] Direct authenticated Supabase requests cannot read or write the other user's rows under RLS.

## Environment and secrets

- [ ] Backend uses `DEPLOYMENT_MODE=online`, the intended pooled database and exact frontend origin.
- [ ] Frontend contains only the Supabase publishable key; no service-role or AI key is present in browser assets.
- [ ] Backend contains the service-role key required for account deletion, never in a `NEXT_PUBLIC` variable.
- [ ] Intended AI provider/model and fallback configuration are active.
- [ ] Local `.env`, SQLite databases, backups and personal test data were not deployed.

## Migrations

- [ ] Deployed migration history matches `supabase/migrations`.
- [ ] `application_decision_json`, `outcome_json`, `release_state_json`, all earlier structured fields and Job Sources exist.
- [ ] RLS is enabled and current policies/grants cover every user-data table.

## Smoke flows

- [ ] Private-sector job: upload → requirements → diagnosis → generation → Final Check → Pack Review → ATS → export → Ready → Applied.
- [ ] Government/Selection Criteria job completes the same path with required confirmations.
- [ ] Exported DOCX and PDF open and the verified artifact identity remains accurate.

## Recovery

- [ ] Refresh and logout/login recover server-backed release state without false readiness.
- [ ] Provider, upload, Pack Review and ATS failures show retryable errors and clear busy states.
- [ ] Session expiry returns to sign-in and reloads saved server state after authentication.

## Account lifecycle

- [ ] Export archive contains the owner's complete data and no other user's data or secrets.
- [ ] Deletion requires the exact destructive confirmation, deletes the Supabase Auth user and removes all owned rows.
- [ ] Another user's account cannot be selected or deleted.

## Quotas

- [ ] Last allowed and first rejected daily/global requests behave correctly.
- [ ] Pack ID retry is idempotent and incomplete packs do not consume quota.
- [ ] Concurrent attempts cannot exceed the intended limit.
- [ ] Day and month boundaries match the documented deployment timezone behavior.

## UI and logging

- [ ] Desktop and one approximately 390px viewport complete the core path without inaccessible controls or horizontal overflow.
- [ ] Logs contain request/correlation ID, operation, pseudonymous user reference, resource ID, provider/model where relevant, duration, status and error category.
- [ ] Logs contain no Resume/JD/generated prose, CKB source text, email, phone, tokens, keys, prompts, responses or outcome notes.
