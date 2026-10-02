-- Server-side Pack credit transactions. These functions are backend-only.

create or replace function public.reserve_pack_credits(
    p_user_id uuid, p_pack_id uuid, p_credit_cost integer, p_monthly_limit integer default 500
) returns table(result_status text, remaining_balance integer)
language plpgsql
security invoker
set search_path = public
as $$
declare
    v_balance integer;
    v_month date := date_trunc('month', timezone('UTC', now()))::date;
    v_reserved integer;
    v_completed integer;
begin
    if p_credit_cost not in (1, 2) then
        raise exception 'Pack credit cost must be 1 or 2';
    end if;

    insert into public.packcreditaccount (user_id, balance) values (p_user_id, 2)
    on conflict (user_id) do nothing;
    if found then
        insert into public.packcreditledger (user_id, entry_type, credits_delta, idempotency_key, note)
        values (p_user_id, 'grant_free', 2, 'grant-free:' || p_user_id::text, 'Initial lifetime credits')
        on conflict (idempotency_key) do nothing;
    end if;

    select balance into v_balance from public.packcreditaccount
    where user_id = p_user_id for update;

    if exists (
        select 1 from public.generationusage
        where user_id = p_user_id and pack_id = p_pack_id
    ) or exists (
        select 1 from public.packcreditledger
        where idempotency_key = 'generation:' || p_user_id::text || ':' || p_pack_id::text
    ) then
        return query select 'existing'::text, v_balance;
        return;
    end if;

    if v_balance < p_credit_cost then
        return query select 'insufficient_credits'::text, v_balance;
        return;
    end if;

    insert into public.globalmonthlyusage (month_start) values (v_month)
    on conflict (month_start) do nothing;
    select reserved_count, completed_count into v_reserved, v_completed
    from public.globalmonthlyusage where month_start = v_month for update;

    if v_reserved + v_completed >= p_monthly_limit then
        return query select 'global_limit'::text, v_balance;
        return;
    end if;

    update public.packcreditaccount
    set balance = balance - p_credit_cost, updated_at = now()
    where user_id = p_user_id;
    insert into public.packcreditledger (
        user_id, entry_type, credits_delta, pack_id, idempotency_key
    ) values (
        p_user_id, 'debit_generation', -p_credit_cost, p_pack_id,
        'generation:' || p_user_id::text || ':' || p_pack_id::text
    );
    insert into public.generationusage (
        user_id, pack_id, generated_at, status, credit_cost, reserved_at, expires_at, usage_month
    ) values (
        p_user_id, p_pack_id, now(), 'reserved', p_credit_cost, now(), now() + interval '30 minutes', v_month
    );
    update public.globalmonthlyusage
    set reserved_count = reserved_count + 1, updated_at = now()
    where month_start = v_month;

    return query select 'reserved'::text, v_balance - p_credit_cost;
end;
$$;

create or replace function public.complete_pack_credits(p_user_id uuid, p_pack_id uuid)
returns boolean language plpgsql security invoker set search_path = public as $$
declare
    v_usage public.generationusage%rowtype;
begin
    select * into v_usage from public.generationusage
    where user_id = p_user_id and pack_id = p_pack_id and status = 'reserved'
    for update;
    if not found then return false; end if;

    update public.generationusage set status = 'completed', completed_at = now()
    where id = v_usage.id;
    update public.globalmonthlyusage
    set reserved_count = reserved_count - 1, completed_count = completed_count + 1, updated_at = now()
    where month_start = v_usage.usage_month and reserved_count > 0;
    return true;
end;
$$;

create or replace function public.release_pack_credits(p_user_id uuid, p_pack_id uuid)
returns boolean language plpgsql security invoker set search_path = public as $$
declare
    v_usage public.generationusage%rowtype;
begin
    select * into v_usage from public.generationusage
    where user_id = p_user_id and pack_id = p_pack_id and status = 'reserved'
    for update;
    if not found then return false; end if;

    update public.generationusage set status = 'released', released_at = now()
    where id = v_usage.id;
    update public.packcreditaccount
    set balance = balance + v_usage.credit_cost, updated_at = now()
    where user_id = p_user_id;
    update public.globalmonthlyusage
    set reserved_count = reserved_count - 1, updated_at = now()
    where month_start = v_usage.usage_month and reserved_count > 0;
    insert into public.packcreditledger (user_id, entry_type, credits_delta, pack_id, idempotency_key)
    values (
        p_user_id, 'release', v_usage.credit_cost, p_pack_id,
        'release:' || p_user_id::text || ':' || p_pack_id::text
    ) on conflict (idempotency_key) do nothing;
    return true;
end;
$$;

create or replace function public.grant_manual_pack_topup(
    p_user_id uuid, p_package_code text, p_credits integer, p_amount_cents integer,
    p_idempotency_key text, p_admin_user_id uuid, p_note text
) returns integer language plpgsql security invoker set search_path = public as $$
declare
    v_balance integer;
    v_existing public.packcreditledger%rowtype;
begin
    if (p_package_code, p_credits, p_amount_cents) not in (
        ('single', 1, 1695), ('starter', 8, 10995), ('job_search', 18, 19900)
    ) then raise exception 'Package metadata does not match the catalog'; end if;

    perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key, 0));
    insert into public.packcreditaccount (user_id, balance) values (p_user_id, 2)
    on conflict (user_id) do nothing;
    if found then
        insert into public.packcreditledger (user_id, entry_type, credits_delta, idempotency_key, note)
        values (p_user_id, 'grant_free', 2, 'grant-free:' || p_user_id::text, 'Initial lifetime credits')
        on conflict (idempotency_key) do nothing;
    end if;
    select balance into v_balance from public.packcreditaccount
    where user_id = p_user_id for update;

    select * into v_existing from public.packcreditledger
    where idempotency_key = p_idempotency_key;
    if found then
        if v_existing.user_id <> p_user_id
            or v_existing.entry_type <> 'grant_manual_topup'
            or v_existing.package_code <> p_package_code
            or v_existing.credits_delta <> p_credits
            or v_existing.amount_cents <> p_amount_cents then
            raise exception 'Idempotency key is already used for a different top-up';
        end if;
        return v_balance;
    end if;

    insert into public.packcreditledger (
        user_id, entry_type, credits_delta, package_code, amount_cents, currency,
        note, idempotency_key, created_by_user_id
    ) values (
        p_user_id, 'grant_manual_topup', p_credits, p_package_code, p_amount_cents, 'AUD',
        p_note, p_idempotency_key, p_admin_user_id
    );
    update public.packcreditaccount set balance = balance + p_credits, updated_at = now()
    where user_id = p_user_id returning balance into v_balance;
    return v_balance;
end;
$$;

revoke all on function public.reserve_pack_credits(uuid, uuid, integer, integer) from public, anon, authenticated;
revoke all on function public.complete_pack_credits(uuid, uuid) from public, anon, authenticated;
revoke all on function public.release_pack_credits(uuid, uuid) from public, anon, authenticated;
revoke all on function public.grant_manual_pack_topup(uuid, text, integer, integer, text, uuid, text) from public, anon, authenticated;
