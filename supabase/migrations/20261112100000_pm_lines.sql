-- Polymarket line movement per NHL game (moneyline, main total, puck line), refreshed every 30 minutes by the
-- pm-sync edge function. Readable by Pro plans only, like the other odds-movement data.
create table if not exists public.pm_lines (
  slug        text primary key,              -- Polymarket event slug, e.g. nhl-phi-edm-2026-11-11
  start_time  timestamptz not null,
  away        text not null,                 -- our team codes (PHI, EDM, ...)
  home        text not null,
  ml          jsonb,                         -- {s:[[unix_min, away_p*1000], ...], vol}
  tot         jsonb,                         -- {line, s:[[unix_min, over_p*1000], ...]}
  pl          jsonb,                         -- {team, line, s:[[unix_min, p*1000], ...]}
  volume      numeric,
  fetched_at  timestamptz not null default now()
);
alter table public.pm_lines enable row level security;
create policy "pro reads polymarket lines" on public.pm_lines for select to authenticated using ((select public.has_pro()));
create index if not exists pm_lines_start on public.pm_lines (start_time);

-- every 30 minutes (applied live as cron job 9):
-- select cron.schedule('pm-sync', '12,42 * * * *', $$ select net.http_post(url := 'https://evxnevqxykbfocqfqxid.supabase.co/functions/v1/pm-sync', headers := '{"content-type":"application/json"}'::jsonb, body := '{}'::jsonb, timeout_milliseconds := 150000) $$);
