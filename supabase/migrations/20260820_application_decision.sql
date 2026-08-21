alter table if exists public.jobapplication
    add column if not exists application_decision_json text not null default '{}';

comment on column public.jobapplication.application_decision_json is
    'Application Decision v1 diagnosis, material confirmations, provenance and input fingerprints.';
