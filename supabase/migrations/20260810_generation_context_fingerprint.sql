alter table generateddocument
add column if not exists context_fingerprint text not null default '';
