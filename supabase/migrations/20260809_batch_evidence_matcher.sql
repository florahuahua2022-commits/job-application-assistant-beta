-- One validated batch match result is shared across all document generators.

alter table if exists public.jobapplication
    add column if not exists evidence_matches_json text not null default '{}';

comment on column public.jobapplication.evidence_matches_json is
    'Evidence Matcher v1 batch result; invalidated when the resume, CKB or Job Model changes.';
