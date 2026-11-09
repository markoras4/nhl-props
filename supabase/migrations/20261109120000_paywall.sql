-- Paywall: plans, Pro checks in the database, and private storage for the Pro data file.
-- See PAYWALL.md and nhlprops/paywall.py. Applied to the live project on 2026-11-09 (migrations "paywall" + "paywall_has_pro_self_only").

-- 1) plans: free (account), pro, creator and media (creator / media = Pro + a content licence)
alter table public.profiles drop constraint if exists profiles_plan_check;
alter table public.profiles add constraint profiles_plan_check check (plan in ('free', 'pro', 'creator', 'media'));
-- users can still only change their display name (column grant), never their plan; the Stripe webhook sets it

-- 2) one helper the policies and functions share
create or replace function public.has_pro(uid uuid default auth.uid())
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles where id = uid and plan in ('pro', 'creator', 'media'));
$$;
revoke all on function public.has_pro(uuid) from public;
grant execute on function public.has_pro(uuid) to authenticated, service_role;

-- 3) watchlist: free accounts follow up to 3 players, Pro unlimited
drop policy if exists "own watchlist: add" on public.watchlist;
create policy "own watchlist: add" on public.watchlist for insert to authenticated
  with check ((select auth.uid()) = user_id
    and ((select public.has_pro()) or (select count(*) from public.watchlist w where w.user_id = (select auth.uid())) < 3));

-- 4) alerts are Pro
drop policy if exists "own alerts: add" on public.alert_settings;
create policy "own alerts: add" on public.alert_settings for insert to authenticated
  with check ((select auth.uid()) = user_id and (select public.has_pro()));
drop policy if exists "own alerts: change" on public.alert_settings;
create policy "own alerts: change" on public.alert_settings for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id and (select public.has_pro()));

-- 5) parlays: free accounts save 2-leg parlays, Pro any size (keeps the existing timing rules)
drop policy if exists "own parlays: add" on public.parlays;
create policy "own parlays: add" on public.parlays for insert to authenticated
  with check ((select auth.uid()) = user_id and result is null and graded_at is null and start_time > (now() + interval '5 minutes')
    and ((select public.has_pro()) or jsonb_array_length(coalesce(legs, '[]'::jsonb)) <= 2));

-- 6) private bucket for the Pro data (written by the daily workflow with the service role, read only via pro-data)
insert into storage.buckets (id, name, public) values ('pro', 'pro', false) on conflict (id) do nothing;

-- 7) has_pro only answers for the signed-in user (no probing other accounts), and not for anon
create or replace function public.has_pro(uid uuid default auth.uid())
returns boolean language sql stable security definer set search_path = public as $$
  select uid is not null and uid = auth.uid()
     and exists (select 1 from public.profiles where id = uid and plan in ('pro', 'creator', 'media'));
$$;
revoke all on function public.has_pro(uuid) from public, anon;
grant execute on function public.has_pro(uuid) to authenticated, service_role;

-- 8) hourly sharp-book (Pinnacle) prices, written by supabase/functions/pin-sync from the GitHub workflow, Pro-only reads
create table if not exists public.pin_odds (
  start_time timestamptz not null, home_name text not null, away_name text not null, player_name text not null,
  market text not null, over numeric not null, under numeric, fetched_at timestamptz not null default now(),
  primary key (start_time, home_name, player_name, market)
);
alter table public.pin_odds enable row level security;
create policy "pro reads sharp prices" on public.pin_odds for select to authenticated using ((select public.has_pro()));
create index if not exists pin_odds_fetched on public.pin_odds (fetched_at desc);
