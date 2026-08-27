alter table if exists public.jobapplication
    add column if not exists archived_at timestamptz;
