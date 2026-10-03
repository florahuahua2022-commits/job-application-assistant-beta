-- Allow admin-only manual custom credit grants with no payment amount.
-- This changes only the existing top-up function; no payment provider is involved.

create or replace function public.grant_manual_pack_topup(
    p_user_id uuid, p_package_code text, p_credits integer, p_amount_cents integer,
    p_idempotency_key text, p_admin_user_id uuid, p_note text
) returns integer language plpgsql security invoker set search_path = public as $$
declare
    v_balance integer;
    v_existing public.packcreditledger%rowtype;
begin
    if not coalesce(
        (p_package_code, p_credits, p_amount_cents) in (
            ('single', 1, 1695), ('starter', 8, 10995), ('job_search', 18, 19900)
        ) or (p_package_code = 'custom' and p_credits > 0 and p_amount_cents = 0),
        false
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

revoke all on function public.grant_manual_pack_topup(uuid, text, integer, integer, text, uuid, text)
    from public, anon, authenticated;
