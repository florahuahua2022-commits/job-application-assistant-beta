-- Read-only aggregates; returns no business-row content.
select a.user_id,a.balance,coalesce(sum(l.credits_delta),0) ledger_balance from public.packcreditaccount a left join public.packcreditledger l on l.user_id=a.user_id group by a.user_id,a.balance having a.balance<>coalesce(sum(l.credits_delta),0);
select r.user_id,r.pack_id,count(d.id) matching_debits,r.credits_delta,max(-d.credits_delta) debited from public.packcreditledger r left join public.packcreditledger d on d.user_id=r.user_id and d.pack_id=r.pack_id and d.entry_type='debit_generation' where r.entry_type='release' group by r.id,r.user_id,r.pack_id,r.credits_delta having count(d.id)<>1 or r.credits_delta<>max(-d.credits_delta);
select count(*) purchase_rows,array_agg(distinct status) statuses,array_agg(distinct currency) currencies,min(created_at) oldest,max(created_at) newest,count(*)-count(distinct stripe_checkout_session_id) duplicate_sessions from public.purchase;
select current_user database_role;
