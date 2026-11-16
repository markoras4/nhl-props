-- Billing: one row per account with its Stripe customer and subscription (see PAYWALL.md, supabase/functions/stripe-*).
-- Only the Edge Functions (service role) write here; the signed-in user can read their own row to show
-- "renews on" / "Pro until" and the Manage subscription button. profiles.plan stays the single source of Pro access:
-- the stripe-webhook function sets it to 'pro' while a subscription is active and back to 'free' when it ends.

create table if not exists public.billing (
  user_id uuid primary key references auth.users (id) on delete cascade,
  stripe_customer_id text unique,
  subscription_id text,
  status text,                  -- Stripe subscription status: active, trialing, past_due, canceled, unpaid, incomplete...
  price_key text,               -- lookup key: pro_monthly, pro_season, founder_monthly, founder_season
  current_period_end timestamptz,
  cancel_at_period_end boolean not null default false,
  updated_at timestamptz not null default now()
);
alter table public.billing enable row level security;

revoke all on public.billing from anon, authenticated;
grant select on public.billing to authenticated;
drop policy if exists "own billing: read" on public.billing;
create policy "own billing: read" on public.billing for select to authenticated using ((select auth.uid()) = user_id);

create index if not exists billing_subscription on public.billing (subscription_id);
