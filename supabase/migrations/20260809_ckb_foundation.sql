-- Versioned Career Knowledge Base storage. Existing resumes remain valid and
-- are backfilled by the backend on their next generation or edit operation.

alter table if exists public.resume
    add column if not exists ckb_json text not null default '[]';

comment on column public.resume.ckb_json is
    'CKB schema v1 evidence records with stable IDs, source provenance and explicit fact verification.';
