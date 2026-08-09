alter table public.generateddocument
    add column if not exists run_id varchar,
    add column if not exists trace_json text not null default '{}';

create index if not exists ix_generateddocument_run_id
    on public.generateddocument (run_id);

comment on column public.generateddocument.trace_json is
    'Privacy-safe generation manifest containing versions, input record references, evidence IDs and review outcome.';
