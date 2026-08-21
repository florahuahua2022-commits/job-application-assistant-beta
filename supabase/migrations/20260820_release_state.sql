alter table public.jobapplication
    add column if not exists release_state_json text not null default '{}';
