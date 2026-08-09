-- Persist explicit user acknowledgement of Transferable and Weak responses.

alter table if exists public.jobapplication
    add column if not exists selection_confirmations_json text not null default '[]';

comment on column public.jobapplication.selection_confirmations_json is
    'Criteria IDs explicitly reviewed by the user; cleared whenever source or generated Selection Criteria changes.';
