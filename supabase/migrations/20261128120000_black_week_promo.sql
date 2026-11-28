-- Black Week 2026 promo: Pro free until 31 Dec 2026 23:59 ET for every existing account and for every account created
-- until 30 Nov 2026 23:59 ET. No card, no Stripe: access comes from profiles.promo_until and ends by itself.
-- Paid plans (profiles.plan, set by stripe-webhook) are untouched; a subscription that ends in December keeps Pro
-- until the promo end through promo_until. See nhlprops/paywall.py (PROMO) and supabase/functions/pro-data.

alter table public.profiles add column if not exists promo_until timestamptz;

-- every account that exists now
update public.profiles set promo_until = '2026-12-31 23:59:59-05' where promo_until is null or promo_until < '2026-12-31 23:59:59-05';

-- new accounts during the sign-up window get it at creation
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, promo_until)
  values (new.id, case when now() <= timestamptz '2026-11-30 23:59:59-05' then timestamptz '2026-12-31 23:59:59-05' end)
  on conflict do nothing;
  return new;
end $$;

-- Pro checks in the database (watchlist, alerts, parlays, sharp prices) count a running promo as Pro
create or replace function public.has_pro(uid uuid default auth.uid())
returns boolean language sql stable security definer set search_path = public as $$
  select uid is not null and uid = auth.uid()
     and exists (select 1 from public.profiles where id = uid
                 and (plan in ('pro', 'creator', 'media') or (promo_until is not null and promo_until > now())));
$$;
revoke all on function public.has_pro(uuid) from public, anon;
grant execute on function public.has_pro(uuid) to authenticated, service_role;
