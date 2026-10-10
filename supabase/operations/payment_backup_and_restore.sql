-- BACKUP: run before migrations. Copies business rows inside this database.
begin;
create schema if not exists private_payment_backup_20261010;
revoke all on schema private_payment_backup_20261010 from public, anon, authenticated;
create table if not exists private_payment_backup_20261010.packcreditaccount as table public.packcreditaccount with data;
create table if not exists private_payment_backup_20261010.packcreditledger as table public.packcreditledger with data;
create table if not exists private_payment_backup_20261010.generationusage as table public.generationusage with data;
create table if not exists private_payment_backup_20261010.purchase as table public.purchase with data;
commit;

select 'packcreditaccount' table_name,count(*) rows from private_payment_backup_20261010.packcreditaccount
union all select 'packcreditledger',count(*) from private_payment_backup_20261010.packcreditledger
union all select 'generationusage',count(*) from private_payment_backup_20261010.generationusage
union all select 'purchase',count(*) from private_payment_backup_20261010.purchase;

-- RESTORE: stop application writes and take a second safety backup first, then uncomment.
-- begin;
-- lock table public.packcreditaccount,public.packcreditledger,public.generationusage,public.purchase in access exclusive mode;
-- truncate public.packcreditledger,public.generationusage,public.purchase,public.packcreditaccount cascade;
-- do $$ declare t text; cols text; begin
--   foreach t in array array['packcreditaccount','purchase','generationusage','packcreditledger'] loop
--     select string_agg(format('%I',c.column_name),',' order by c.ordinal_position) into cols
--     from information_schema.columns c join information_schema.columns b
--       on b.table_schema='private_payment_backup_20261010' and b.table_name=t and b.column_name=c.column_name
--     where c.table_schema='public' and c.table_name=t;
--     execute format('insert into public.%I(%s) select %s from private_payment_backup_20261010.%I',t,cols,cols,t);
--   end loop;
-- end $$;
-- select setval(pg_get_serial_sequence('public.purchase','id'),coalesce(max(id),1),max(id) is not null) from public.purchase;
-- select setval(pg_get_serial_sequence('public.packcreditledger','id'),coalesce(max(id),1),max(id) is not null) from public.packcreditledger;
-- select setval(pg_get_serial_sequence('public.generationusage','id'),coalesce(max(id),1),max(id) is not null) from public.generationusage;
-- commit;
