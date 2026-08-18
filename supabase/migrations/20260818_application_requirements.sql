-- Parsed submission-document requirements. Stored for confirmation, but not yet used for generation routing.

alter table if exists public.jobapplication
    add column if not exists application_requirements_json text not null default '{}';

comment on column public.jobapplication.application_requirements_json is
    'Versioned Application Requirements model: required documents, placement, limits, provenance and confirmation state.';
