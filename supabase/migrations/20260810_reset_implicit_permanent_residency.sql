-- Older releases defaulted work_rights to permanent_resident without an
-- explicit applicant declaration. Fail safely and require users to confirm it.
update applicantprofile
set work_rights = 'not_specified'
where work_rights = 'permanent_resident';

alter table applicantprofile
alter column work_rights set default 'not_specified';
