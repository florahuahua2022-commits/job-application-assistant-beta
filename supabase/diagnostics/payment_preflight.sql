-- One read-only SELECT. Any ERROR row blocks rollout.
with checks as (
    select 'pre_2b_exact_balance_mismatch' check_name, 'ERROR' severity, count(*) issue_count
    from (
        select a.user_id from public.packcreditaccount a
        left join public.packcreditledger l on l.user_id=a.user_id
        group by a.user_id,a.balance having a.balance <>
            coalesce(sum(l.credits_delta) filter (where l.entry_type in
                ('grant_free','grant_manual_topup','grant_stripe_purchase')),0)
            - coalesce(-sum(l.credits_delta) filter (where l.entry_type='debit_generation'
                and exists (select 1 from public.generationusage u
                    where u.user_id=l.user_id and u.pack_id=l.pack_id and u.status <> 'released')),0)
    ) x
    union all
    select 'account_ledger_mismatch' check_name, 'ERROR' severity, count(*) issue_count
    from (
        select a.user_id from public.packcreditaccount a
        left join public.packcreditledger l on l.user_id=a.user_id
        group by a.user_id,a.balance having a.balance<>coalesce(sum(l.credits_delta),0)
    ) x
    union all
    select 'ambiguous_release', 'ERROR', count(*) from (
        select r.id from public.packcreditledger r
        left join public.packcreditledger d on d.user_id=r.user_id and d.pack_id=r.pack_id
            and d.entry_type='debit_generation'
        where r.entry_type='release' group by r.id,r.credits_delta
        having count(d.id)<>1 or r.credits_delta<>max(-d.credits_delta)
    ) x
    union all
    select 'backfill_debit_without_usage', 'ERROR', count(*)
    from public.packcreditledger d where d.entry_type='debit_generation' and not exists (
        select 1 from public.generationusage u where u.user_id=d.user_id and u.pack_id=d.pack_id
    )
    union all
    select 'backfill_insufficient_grants', 'ERROR', count(*) from (
        select user_id from public.packcreditledger group by user_id
        having coalesce(sum(credits_delta) filter (where entry_type in
            ('grant_free','grant_manual_topup','grant_stripe_purchase')),0)
          < coalesce(-sum(credits_delta) filter (where entry_type='debit_generation'),0)
            - coalesce(sum(credits_delta) filter (where entry_type='release'),0)
    ) x
    union all
    select 'duplicate_checkout_session', 'ERROR', count(*) from (
        select stripe_checkout_session_id from public.purchase
        group by stripe_checkout_session_id having count(*)>1
    ) x
    union all
    select 'purchase_rows_unknown_livemode', 'ERROR', case when exists (
        select 1 from information_schema.columns where table_schema='public'
          and table_name='purchase' and column_name='livemode'
    ) then 0 else count(*) end from public.purchase
    union all
    select 'stripeevent_rows_unknown_livemode', 'ERROR', coalesce((
        select case when exists (
            select 1 from information_schema.columns where table_schema='public'
              and table_name='stripeevent' and column_name='livemode'
        ) then 0 else greatest(c.reltuples,0)::bigint end
        from pg_class c join pg_namespace n on n.oid=c.relnamespace
        where n.nspname='public' and c.relname='stripeevent'
    ),0)
    union all
    select 'post_2b_balance_lot_reservation_mismatch', 'ERROR',
        case when to_regclass('public.packcreditlot') is null
               or to_regclass('public.packcreditallocation') is null then 0
        else cardinality(xpath('/root/table/row', xmlelement(name root, query_to_xml($check$
            select 1 from public.packcreditaccount a
            left join (select user_id,sum(remaining_credits) remaining
                       from public.packcreditlot group by user_id) l on l.user_id=a.user_id
            left join (select user_id,sum(credits) reserved
                       from public.packcreditallocation where status='reserved' group by user_id) r
                on r.user_id=a.user_id
            where a.balance <> coalesce(l.remaining,0) + coalesce(r.reserved,0)
        $check$, false, true, '')))) end
), inventory as (
    select 'purchase_status:'||status check_name, 'INFO' severity, count(*) issue_count
    from public.purchase group by status
)
select check_name, severity, issue_count,
    case when severity='ERROR' and issue_count>0 then 'BLOCK' else 'OK' end result
from (select * from checks union all select * from inventory) report
order by severity desc, check_name;
