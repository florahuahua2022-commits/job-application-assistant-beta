alter table if exists public.jobsource
    add column if not exists discovery_context text not null default '';
