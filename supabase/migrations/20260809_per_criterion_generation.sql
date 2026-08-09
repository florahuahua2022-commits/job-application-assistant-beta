-- Preserve auditable per-criterion STAR plans and validated generator responses.

alter table if exists public.generateddocument
    add column if not exists structured_content_json text not null default '{}';

comment on column public.generateddocument.structured_content_json is
    'Validated per-criterion JSON responses; final user-facing prose remains in content.';
