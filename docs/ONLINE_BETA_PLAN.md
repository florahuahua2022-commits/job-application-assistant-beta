# Online Beta Plan

## Goal

Create a low-cost, invitation-only online beta for 5-10 Australian test users while keeping the existing Windows local version working and keeping the owner's personal data and API keys out of the deployment.

## Proposed beta services

- Frontend and FastAPI backend: Render free services during the closed beta.
- Authentication and database: Supabase Auth and Postgres free plan.
- AI generation: DeepSeek only, called from the FastAPI backend.
- File handling: extract uploaded resumes in memory, save extracted text to the user's database record, and do not retain the original upload unless storage is added deliberately later.

The free services are suitable for testing, not a production launch. A sleeping backend may take about a minute to wake. The beta must not promise continuous availability.

## Non-negotiable safeguards

1. No public sign-up. Test accounts are invited or allow-listed.
2. Every profile, resume, application and generated document has an authenticated `user_id`.
3. Every read, update, export and delete operation checks ownership.
4. Row Level Security is enabled for every user-data table in Supabase.
5. The Supabase service key and DeepSeek API key remain server-side only.
6. The local `.env`, SQLite database, backups and personal application data are never deployed.
7. Limit each beta user to three application packs per day by default.
8. Add a global monthly generation ceiling and stop generation when it is reached.
9. Do not log resume text, cover letters, contact details or API keys.
10. Give users a visible way to delete their profile, resume, applications and generated documents.
11. The user reviews all facts and performs the final external submission.

## Migration phases

### Phase 1 - Cloud-ready code

- Add a `DEPLOYMENT_MODE=local|online` setting.
- Preserve the current SQLite behaviour in local mode.
- Add authenticated user context in online mode.
- Add ownership fields and ownership filters to all user data.
- Replace local backup/restore with account data export/delete in online mode.
- Add generation quota records and enforce daily/global limits before calling DeepSeek.

### Phase 2 - Supabase test project

- Create a new Supabase project containing no local data.
- Configure email login and invitation-only access.
- Create Postgres tables and RLS policies.
- Test two accounts to prove that one user cannot access the other user's records.

### Phase 3 - Private deployment

- Deploy the backend and frontend to private beta URLs.
- Add server-only environment variables.
- Disable OpenAI fallback for the beta.
- Create two tester accounts and set conservative quotas.
- Test sign-in, upload, generation, export, deletion and logout.

### Phase 4 - Small beta

- Invite 5-10 users.
- Monitor only request counts, failures, latency and aggregate AI usage.
- Do not inspect user documents unless a user deliberately sends one for support.
- Review feedback before accepting additional users.

## Accounts the owner will need

- A free Supabase account and project.
- A free Render account.
- A source-code repository used for deployment, with all secrets and personal data excluded.
- A DeepSeek API account with a deliberately small prepaid balance or spending limit.

## Release gate

Do not send the beta link to a tester until all of these pass:

- Two-user data-isolation test.
- API keys absent from browser code and deployment logs.
- Daily and global generation limits verified.
- Account deletion verified.
- Resume upload and generated-document export verified.
- Privacy notice and beta disclaimer visible.
- Local Windows version still starts and passes its existing tests.

