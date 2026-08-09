-- Persist the semantic/factual Reviewer decision separately from generated prose.

alter table if exists public.generateddocument
    add column if not exists reviewer_json text not null default '{}';

comment on column public.generateddocument.reviewer_json is
    'Batch Reviewer v1 decisions and issue categories; Reviewer never mutates generated content.';
