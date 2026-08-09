alter table public.applicantprofile
    add column if not exists target_direction text,
    add column if not exists motivation text,
    add column if not exists writing_tone varchar not null default 'natural_professional',
    add column if not exists preferences_notes text;

comment on column public.applicantprofile.motivation is
    'User-declared intent only; it is not Career Knowledge Base evidence.';
