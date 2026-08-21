alter table if exists public.jobapplication
    add column if not exists outcome_json text not null default '{}';

comment on column public.jobapplication.outcome_json is
    'Outcome Learning v1 manual event history and immutable submission strategy snapshot; never applicant evidence.';
