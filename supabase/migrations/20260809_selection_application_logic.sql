-- Deterministic word allocations, evidence status and reuse diagnostics.

alter table if exists public.jobapplication
    add column if not exists selection_plan_json text not null default '{}';

comment on column public.jobapplication.selection_plan_json is
    'Selection Plan v1 built deterministically from the Job Model, batch matches and CKB.';
