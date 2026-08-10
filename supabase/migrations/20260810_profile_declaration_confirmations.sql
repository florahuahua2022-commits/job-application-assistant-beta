alter table applicantprofile
add column if not exists work_rights_confirmed boolean not null default false,
add column if not exists availability_confirmed boolean not null default false,
add column if not exists motivation_confirmed boolean not null default false;

update applicantprofile
set work_rights = 'not_specified',
    work_rights_confirmed = false,
    availability_notice = 'not_specified',
    availability_confirmed = false,
    motivation = null,
    motivation_confirmed = false;
