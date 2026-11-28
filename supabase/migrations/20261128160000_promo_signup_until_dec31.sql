-- Black Week promo: free Pro sign-ups now run until 31 Dec 2026 23:59 ET (was 30 Nov). Access still ends 31 Dec.
create or replace function public.handle_new_user() returns trigger language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, promo_until)
  values (new.id, case when now() <= timestamptz '2026-12-31 23:59:59-05' then timestamptz '2026-12-31 23:59:59-05' end)
  on conflict do nothing;
  return new;
end $$;
