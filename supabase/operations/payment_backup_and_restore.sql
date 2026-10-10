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
commit;

select 'packcreditaccount' table_name,count(*) rows from private_payment_backup_20261010.packcreditaccount
union all select 'packcreditledger',count(*) from private_payment_backup_20261010.packcreditledger
union all select 'generationusage',count(*) from private_payment_backup_20261010.generationusage
union all select 'purchase',count(*) from private_payment_backup_20261010.purchase;

-- Restore is intentionally a separate guarded script: payment_restore.sql.
-- Never restore over post-backup payments or generations; reconcile/export those writes first.
