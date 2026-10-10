-- BACKUP: run before migrations. Copies business rows inside this database.
begin;
do $$ begin
  if to_regnamespace('private_payment_backup_20261010') is not null then
    raise exception 'Backup schema already exists; stop rather than reuse a stale snapshot';
  end if;
end $$;
create schema private_payment_backup_20261010;
revoke all on schema private_payment_backup_20261010 from public, anon, authenticated;
create table private_payment_backup_20261010.packcreditaccount as table public.packcreditaccount with data;
create table private_payment_backup_20261010.packcreditledger as table public.packcreditledger with data;
create table private_payment_backup_20261010.generationusage as table public.generationusage with data;
create table private_payment_backup_20261010.purchase as table public.purchase with data;
create table private_payment_backup_20261010.globalmonthlyusage as table public.globalmonthlyusage with data;
commit;

select table_name, live_rows, backup_rows,
  case when live_rows=backup_rows then 'MATCH' else 'MISMATCH' end result
from (values
  ('packcreditaccount', (select count(*) from public.packcreditaccount),
    (select count(*) from private_payment_backup_20261010.packcreditaccount)),
  ('packcreditledger', (select count(*) from public.packcreditledger),
    (select count(*) from private_payment_backup_20261010.packcreditledger)),
  ('generationusage', (select count(*) from public.generationusage),
    (select count(*) from private_payment_backup_20261010.generationusage)),
  ('purchase', (select count(*) from public.purchase),
    (select count(*) from private_payment_backup_20261010.purchase)),
  ('globalmonthlyusage', (select count(*) from public.globalmonthlyusage),
    (select count(*) from private_payment_backup_20261010.globalmonthlyusage))
) counts(table_name,live_rows,backup_rows);

-- These copies are evidence for manual reconciliation only. Do not restore them automatically.
