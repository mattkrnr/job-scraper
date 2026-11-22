-- Applied to Supabase project "Personal" (lvaxrikegqknfkzrvmbr). Depends on private.is_owner() from the shared apps schema.
create table public.job_searches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  name text not null,
  active boolean not null default true,
  origin text not null,                       -- ZIP or address; unique per search, not per user
  radius_miles int not null default 25 check (radius_miles between 1 and 200),
  titles text[] not null default '{}',        -- search terms, each scraped separately
  must_include text[] not null default '{}',  -- any of these words must appear in title
  exclude_words text[] not null default '{}', -- drop jobs whose title has any of these
  min_salary int,                             -- yearly USD
  include_no_salary boolean not null default true,
  work_modes text[] not null default '{remote,hybrid,onsite}',
  experience_levels text[] not null default '{}', -- empty = any; entry, mid, senior, lead
  job_types text[] not null default '{}',         -- empty = any; fulltime, parttime, contract, internship
  hours_old int not null default 72 check (hours_old between 1 and 720),
  sites text[] not null default '{indeed,google}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_run_at timestamptz,
  last_run_status text,
  last_run_new int
);
comment on table public.job_searches is 'Job Scraper: saved searches (filters). One user can have many; origin is per search.';

create table public.job_matches (
  id uuid primary key default gen_random_uuid(),
  search_id uuid not null references public.job_searches(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  job_key text not null,
  title text, company text, location text,
  work_mode text, experience_level text, job_type text,
  salary_min numeric, salary_max numeric,     -- yearly USD
  site text, job_url text, description text,
  date_posted date,
  found_at timestamptz not null default now(),
  status text not null default 'new' check (status in ('new','saved','applied','dismissed')),
  notified_at timestamptz,                    -- Phase 2 email
  unique (search_id, job_key)
);
comment on table public.job_matches is 'Job Scraper: jobs that matched a saved search. Written by the scheduled scraper.';
create index on public.job_matches (search_id, found_at desc);
create index on public.job_matches (user_id);
create index on public.job_searches (user_id);

create table public.job_scraper_tokens (
  token_hash bytea primary key,
  created_at timestamptz not null default now()
);
comment on table public.job_scraper_tokens is 'Hashes of Job Scraper tokens (GitHub Action). No permissive policies: only security-definer functions touch it.';

alter table public.job_searches enable row level security;
alter table public.job_matches enable row level security;
alter table public.job_scraper_tokens enable row level security;

create policy "own rows" on public.job_searches for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "owner only" on public.job_searches as restrictive for all to authenticated
  using ((select private.is_owner())) with check ((select private.is_owner()));
create policy "own rows" on public.job_matches for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "owner only" on public.job_matches as restrictive for all to authenticated
  using ((select private.is_owner())) with check ((select private.is_owner()));
create policy "owner only" on public.job_scraper_tokens as restrictive for all to authenticated
  using ((select private.is_owner())) with check ((select private.is_owner()));

create or replace function private.job_touch_updated() returns trigger language plpgsql set search_path = '' as $$
begin new.updated_at = now(); return new; end $$;
create trigger job_searches_touch before update on public.job_searches
  for each row execute function private.job_touch_updated();

create or replace function private.job_token_ok(p_token text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.job_scraper_tokens t
                 where t.token_hash = sha256(convert_to(coalesce(p_token,''), 'UTF8')))
$$;

-- Scraper: read active searches
create or replace function public.job_scraper_searches(p_token text)
returns setof public.job_searches
language plpgsql stable security definer set search_path = '' as $$
begin
  if not private.job_token_ok(p_token) then raise exception 'bad token' using errcode = '28000'; end if;
  return query select * from public.job_searches where active order by created_at;
end $$;

-- Scraper: save matches for one search; returns number of new rows
create or replace function public.job_scraper_save(p_token text, p_search_id uuid, p_jobs jsonb, p_status text)
returns int
language plpgsql security definer set search_path = '' as $$
declare v_user uuid; v_new int := 0;
begin
  if not private.job_token_ok(p_token) then raise exception 'bad token' using errcode = '28000'; end if;
  select user_id into v_user from public.job_searches where id = p_search_id;
  if v_user is null then raise exception 'unknown search'; end if;

  with ins as (
    insert into public.job_matches (search_id, user_id, job_key, title, company, location, work_mode,
      experience_level, job_type, salary_min, salary_max, site, job_url, description, date_posted)
    select p_search_id, v_user, j->>'job_key', j->>'title', j->>'company', j->>'location', j->>'work_mode',
      j->>'experience_level', j->>'job_type', (j->>'salary_min')::numeric, (j->>'salary_max')::numeric,
      j->>'site', j->>'job_url', left(j->>'description', 4000), (j->>'date_posted')::date
    from jsonb_array_elements(coalesce(p_jobs, '[]'::jsonb)) j
    where coalesce(j->>'job_key','') <> ''
    on conflict (search_id, job_key) do nothing
    returning 1)
  select count(*) into v_new from ins;

  update public.job_searches set last_run_at = now(), last_run_status = left(p_status, 500), last_run_new = v_new
   where id = p_search_id;
  return v_new;
end $$;

revoke all on function public.job_scraper_searches(text) from public, authenticated;
revoke all on function public.job_scraper_save(text, uuid, jsonb, text) from public, authenticated;
grant execute on function public.job_scraper_searches(text) to anon;
grant execute on function public.job_scraper_save(text, uuid, jsonb, text) to anon;
revoke all on function private.job_token_ok(text) from public;
grant execute on function private.job_token_ok(text) to anon;
