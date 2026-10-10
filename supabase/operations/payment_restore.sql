-- DESTRUCTIVE RECOVERY ONLY. Stop all application writes and preserve any post-backup rows first.
-- This cannot merge payments or generations created after the snapshot.
begin;
do $$ begin
  if to_regnamespace('private_payment_backup_20261010') is null then
    raise exception 'Backup schema is missing';
  end if;
  if exists (select 1 from public.purchase p where not exists (
      select 1 from private_payment_backup_20261010.purchase b where b.id=p.id))
    or exists (select 1 from public.generationusage u where not exists (
      select 1 from private_payment_backup_20261010.generationusage b where b.id=u.id)) then
    raise exception 'Post-backup business writes exist; restore would lose them';
  end if;
end $$;
lock table public.packcreditaccount,public.packcreditledger,public.generationusage,public.purchase
  in access exclusive mode;
truncate public.packcreditledger,public.generationusage,public.purchase,public.packcreditaccount cascade;
do $$ declare t text; cols text; begin
  foreach t in array array['packcreditaccount','purchase','generationusage','packcreditledger'] loop
    select string_agg(format('%I',c.column_name),',' order by c.ordinal_position) into cols
    from information_schema.columns c join information_schema.columns b
      on b.table_schema='private_payment_backup_20261010' and b.table_name=t and b.column_name=c.column_name
    where c.table_schema='public' and c.table_name=t;
    execute format('insert into public.%I(%s) select %s from private_payment_backup_20261010.%I',t,cols,cols,t);
  end loop;
end $$;
select setval(pg_get_serial_sequence('public.purchase','id'),coalesce(max(id),1),max(id) is not null) from public.purchase;
select setval(pg_get_serial_sequence('public.packcreditledger','id'),coalesce(max(id),1),max(id) is not null) from public.packcreditledger;
select setval(pg_get_serial_sequence('public.generationusage','id'),coalesce(max(id),1),max(id) is not null) from public.generationusage;
commit;
