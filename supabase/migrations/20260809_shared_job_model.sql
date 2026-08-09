-- Consumer-neutral, versioned JD parsing result shared by all document pipelines.

alter table if exists public.jobapplication
    add column if not exists job_model_json text not null default '{}';

comment on column public.jobapplication.job_model_json is
    'Shared Job Model v1: parsed criteria, categories, competencies and exact word-limit mode.';
