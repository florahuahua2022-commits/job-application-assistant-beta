begin;

lock table public.packcreditaccount, public.generationusage, public.purchase,
    public.packcreditledger in share row exclusive mode;

alter table public.purchase
    add column if not exists package_code text,
    add column if not exists subtotal_cents integer,
    add column if not exists gst_cents integer not null default 0,
    add column if not exists total_paid_cents integer,
    add column if not exists single_pack_price_cents integer,
    add column if not exists actual_stripe_fee_cents integer,
    add column if not exists stripe_charge_id text,
    add column if not exists stripe_balance_transaction_id text,
    add column if not exists paid_at timestamptz,
    add column if not exists dispute_status text not null default 'none';

alter table public.purchase drop constraint if exists purchase_currency_check;
update public.purchase
set total_paid_cents = amount_cents,
    subtotal_cents = amount_cents,
    currency = upper(currency)
where total_paid_cents is null or subtotal_cents is null or currency <> upper(currency);
alter table public.purchase add constraint purchase_currency_check check (currency = 'AUD');
create unique index if not exists purchase_stripe_charge_id_key
    on public.purchase(stripe_charge_id) where stripe_charge_id is not null;
create unique index if not exists purchase_stripe_balance_transaction_id_key
    on public.purchase(stripe_balance_transaction_id) where stripe_balance_transaction_id is not null;

alter table public.packcreditledger
    add column if not exists purchase_id bigint references public.purchase(id);
alter table public.packcreditledger drop constraint if exists packcreditledger_entry_type_check;
alter table public.packcreditledger add constraint packcreditledger_entry_type_check check (
    entry_type in ('grant_free', 'grant_manual_topup', 'grant_stripe_purchase', 'debit_generation', 'release')
);

create table if not exists public.stripeevent (
    stripe_event_id text primary key,
    event_type text not null,
    facts_fingerprint text not null,
    purchase_id bigint references public.purchase(id),
    status text not null check (status in ('received', 'processing', 'processed', 'failed')),
    attempt_count integer not null default 0,
    last_error text,
    processed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.purchase enable row level security;
alter table public.stripeevent enable row level security;
drop policy if exists "purchase_owner_read" on public.purchase;
revoke all on public.purchase, public.stripeevent from public, anon, authenticated;

create or replace function public.get_available_pack_credits(p_user_id uuid)
returns integer language sql security invoker set search_path = public as $$
    select balance from public.packcreditaccount where user_id = p_user_id
$$;

create or replace function public.grant_pack_credits(
    p_user_id uuid, p_source text, p_package_code text, p_credits integer,
    p_amount integer, p_key text, p_purchase bigint, p_admin uuid, p_note text
) returns integer language plpgsql security invoker set search_path = public as $$
declare
    v_balance integer;
    v_existing public.packcreditledger%rowtype;
    v_entry_type text;
begin
    if p_source not in ('manual', 'stripe') or p_credits <= 0 then raise exception 'Invalid grant'; end if;
    if (p_source = 'manual' and (p_admin is null or p_purchase is not null))
        or (p_source = 'stripe' and (p_purchase is null or p_admin is not null)) then
        raise exception 'Invalid grant owner';
    end if;
    v_entry_type := case when p_source = 'stripe' then 'grant_stripe_purchase' else 'grant_manual_topup' end;
    perform pg_advisory_xact_lock(hashtextextended(p_key, 0));
    insert into public.packcreditaccount(user_id, balance) values (p_user_id, 2) on conflict do nothing;
    if found then
        insert into public.packcreditledger(user_id, entry_type, credits_delta, idempotency_key, note)
        values (p_user_id, 'grant_free', 2, 'grant-free:' || p_user_id, 'Initial lifetime credits')
        on conflict do nothing;
    end if;
    select balance into v_balance from public.packcreditaccount where user_id = p_user_id for update;
    select * into v_existing from public.packcreditledger where idempotency_key = p_key;
    if found then
        if v_existing.user_id <> p_user_id or v_existing.entry_type <> v_entry_type
            or v_existing.package_code <> p_package_code or v_existing.credits_delta <> p_credits
            or v_existing.amount_cents <> p_amount or v_existing.purchase_id is distinct from p_purchase
            or v_existing.created_by_user_id is distinct from p_admin then
            raise exception 'Idempotency conflict';
        end if;
        return v_balance;
    end if;
    insert into public.packcreditledger(
        user_id, entry_type, credits_delta, package_code, amount_cents, currency,
        note, idempotency_key, created_by_user_id, purchase_id
    ) values (p_user_id, v_entry_type, p_credits, p_package_code, p_amount, 'AUD',
        p_note, p_key, p_admin, p_purchase);
    update public.packcreditaccount set balance = balance + p_credits, updated_at = now()
    where user_id = p_user_id returning balance into v_balance;
    return v_balance;
end;
$$;

create or replace function public.grant_manual_pack_topup(
    p_user_id uuid, p_package_code text, p_credits integer, p_amount_cents integer,
    p_idempotency_key text, p_admin_user_id uuid, p_note text
) returns integer language plpgsql security invoker set search_path = public as $$
begin
    if not coalesce((p_package_code, p_credits, p_amount_cents) in (
        ('single', 1, 1695), ('starter', 8, 10995), ('job_search', 18, 19900)
    ) or (p_package_code = 'custom' and p_credits > 0 and p_amount_cents = 0), false) then
        raise exception 'Package metadata does not match the catalog';
    end if;
    return public.grant_pack_credits(p_user_id, 'manual', p_package_code, p_credits,
        p_amount_cents, p_idempotency_key, null, p_admin_user_id, p_note);
end;
$$;

create or replace function public.process_stripe_purchase_event(
    p_event_id text, p_fingerprint text, p_session_id text, p_payment_intent_id text,
    p_user_id uuid, p_package_code text, p_credits integer, p_subtotal integer,
    p_gst integer, p_total integer, p_single_price integer, p_currency text
) returns integer language plpgsql security invoker set search_path = public as $$
declare
    v_event public.stripeevent%rowtype;
    v_purchase public.purchase%rowtype;
    v_balance integer;
begin
    if p_currency <> 'AUD' or p_credits <= 0 or p_total <= 0
        or p_subtotal + p_gst <> p_total or p_single_price <= 0 then raise exception 'Invalid purchase facts'; end if;
    perform pg_advisory_xact_lock(hashtextextended(p_event_id, 0));
    select * into v_event from public.stripeevent where stripe_event_id = p_event_id for update;
    if found and v_event.facts_fingerprint <> p_fingerprint then raise exception 'Event facts conflict'; end if;
    if found and v_event.status = 'processed' then return public.get_available_pack_credits(p_user_id); end if;
    insert into public.stripeevent(stripe_event_id, event_type, facts_fingerprint, status, attempt_count, last_error)
    values (p_event_id, 'checkout.session.completed', p_fingerprint, 'processing', 1, null)
    on conflict (stripe_event_id) do update set status = 'processing',
        attempt_count = stripeevent.attempt_count + 1, last_error = null, updated_at = now();

    select * into v_purchase from public.purchase where stripe_checkout_session_id = p_session_id for update;
    if found then
        if v_purchase.user_id <> p_user_id
            or v_purchase.stripe_payment_intent_id is distinct from p_payment_intent_id
            or v_purchase.package_code <> p_package_code or v_purchase.credits <> p_credits
            or v_purchase.subtotal_cents <> p_subtotal or v_purchase.gst_cents <> p_gst
            or v_purchase.total_paid_cents <> p_total
            or v_purchase.single_pack_price_cents <> p_single_price or v_purchase.currency <> p_currency then
            raise exception 'Checkout session facts conflict';
        end if;
    else
        insert into public.purchase(user_id, stripe_checkout_session_id, stripe_payment_intent_id,
            status, package_code, currency, credits, amount_cents, subtotal_cents, gst_cents,
            total_paid_cents, single_pack_price_cents)
        values (p_user_id, p_session_id, p_payment_intent_id, 'pending', p_package_code,
            p_currency, p_credits, p_total, p_subtotal, p_gst, p_total, p_single_price)
        returning * into v_purchase;
    end if;
    v_balance := public.grant_pack_credits(p_user_id, 'stripe', p_package_code, p_credits,
        p_total, 'stripe-purchase:' || p_session_id, v_purchase.id, null, null);
    update public.purchase set status = 'paid', paid_at = coalesce(paid_at, now()), updated_at = now()
    where id = v_purchase.id;
    update public.stripeevent set purchase_id = v_purchase.id, status = 'processed',
        processed_at = now(), updated_at = now() where stripe_event_id = p_event_id;
    return v_balance;
end;
$$;

create or replace function public.record_stripe_event_failure(p_id text, p_fingerprint text, p_error text)
returns void language plpgsql security invoker set search_path = public as $$
declare v_fingerprint text;
begin
    perform pg_advisory_xact_lock(hashtextextended(p_id, 0));
    select facts_fingerprint into v_fingerprint from public.stripeevent where stripe_event_id = p_id for update;
    if found and v_fingerprint <> p_fingerprint then raise exception 'Event facts conflict'; end if;
    insert into public.stripeevent(stripe_event_id, event_type, facts_fingerprint, status, attempt_count, last_error)
    values (p_id, 'checkout.session.completed', p_fingerprint, 'failed', 1, left(p_error, 1000))
    on conflict (stripe_event_id) do update set status = 'failed',
        attempt_count = stripeevent.attempt_count + 1, last_error = left(p_error, 1000), updated_at = now();
end;
$$;

revoke all on function public.get_available_pack_credits(uuid) from public, anon, authenticated;
revoke all on function public.grant_pack_credits(uuid, text, text, integer, integer, text, bigint, uuid, text) from public, anon, authenticated;
revoke all on function public.grant_manual_pack_topup(uuid, text, integer, integer, text, uuid, text) from public, anon, authenticated;
revoke all on function public.process_stripe_purchase_event(text, text, text, text, uuid, text, integer, integer, integer, integer, integer, text) from public, anon, authenticated;
revoke all on function public.record_stripe_event_failure(text, text, text) from public, anon, authenticated;
grant execute on function public.get_available_pack_credits(uuid) to service_role;
grant execute on function public.grant_pack_credits(uuid, text, text, integer, integer, text, bigint, uuid, text) to service_role;
grant execute on function public.grant_manual_pack_topup(uuid, text, integer, integer, text, uuid, text) to service_role;
grant execute on function public.process_stripe_purchase_event(text, text, text, text, uuid, text, integer, integer, integer, integer, integer, text) to service_role;
grant execute on function public.record_stripe_event_failure(text, text, text) to service_role;

commit;
