-- Read-only. Run in Supabase SQL Editor before migration; do not edit data.
begin transaction read only;

select 'purchase_rows' as check_name, count(*)::text as value from public.purchase
union all
select 'stripeevent_rows', count(*)::text from public.stripeevent;

select status, count(*) from public.purchase group by status order by status;
select status, count(*) from public.stripeevent group by status order by status;

select stripe_checkout_session_id, count(*)
from public.purchase
group by stripe_checkout_session_id having count(*) > 1;

select schemaname, tablename, rowsecurity
from pg_tables
where schemaname = 'public' and tablename in ('purchase', 'stripeevent');

select grantee, table_name, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name in ('purchase', 'stripeevent')
  and grantee in ('PUBLIC', 'anon', 'authenticated', 'service_role')
order by table_name, grantee, privilege_type;

rollback;
