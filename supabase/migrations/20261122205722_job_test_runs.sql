create table public.job_test_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  search_id uuid references public.job_searches(id) on delete set null, -- null for unsaved drafts
  params jsonb not null,                 -- search fields as they were in the editor
  status text not null default 'queued' check (status in ('queued','running','done','error')),
  requested_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  message text,
  results jsonb                          -- [{...match, kept: bool, reason: text}]
);
comment on table public.job_test_runs is 'Job Scraper: test runs of a search (preview only, not saved as matches). Processed by the 5-minute GitHub Action.';
create index on public.job_test_runs (user_id, requested_at desc);
create unique index job_test_runs_one_open_per_user on public.job_test_runs (user_id) where status in ('queued','running');

alter table public.job_test_runs enable row level security;
create policy "own rows" on public.job_test_runs for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "owner only" on public.job_test_runs as restrictive for all to authenticated
  using ((select private.is_owner())) with check ((select private.is_owner()));

-- Cheap check so the workflow can exit before installing anything
create or replace function public.job_scraper_pending_tests(p_token text) returns int
language plpgsql stable security definer set search_path = '' as $$
begin
  if not private.job_token_ok(p_token) then raise exception 'bad token' using errcode = '28000'; end if;
  return (select count(*) from public.job_test_runs
          where status = 'queued' or (status = 'running' and started_at < now() - interval '30 minutes'));
end $$;

-- Claim queued (or stuck) tests; also clears tests older than 7 days
create or replace function public.job_scraper_claim_tests(p_token text)
returns table (id uuid, params jsonb)
language plpgsql security definer set search_path = '' as $$
begin
  if not private.job_token_ok(p_token) then raise exception 'bad token' using errcode = '28000'; end if;
  delete from public.job_test_runs t where t.requested_at < now() - interval '7 days';
  return query
  update public.job_test_runs t set status = 'running', started_at = now()
   where t.status = 'queued' or (t.status = 'running' and t.started_at < now() - interval '30 minutes')
  returning t.id, t.params;
end $$;

create or replace function public.job_scraper_finish_test(p_token text, p_id uuid, p_results jsonb, p_ok boolean, p_message text)
returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not private.job_token_ok(p_token) then raise exception 'bad token' using errcode = '28000'; end if;
  update public.job_test_runs set status = case when p_ok then 'done' else 'error' end,
    finished_at = now(), results = p_results, message = left(p_message, 1000)
   where id = p_id;
end $$;

revoke all on function public.job_scraper_pending_tests(text) from public, authenticated;
revoke all on function public.job_scraper_claim_tests(text) from public, authenticated;
revoke all on function public.job_scraper_finish_test(text, uuid, jsonb, boolean, text) from public, authenticated;
grant execute on function public.job_scraper_pending_tests(text) to anon;
grant execute on function public.job_scraper_claim_tests(text) to anon;
grant execute on function public.job_scraper_finish_test(text, uuid, jsonb, boolean, text) to anon;
