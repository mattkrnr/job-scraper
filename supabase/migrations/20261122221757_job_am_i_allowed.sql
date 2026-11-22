-- Lets the app tell a signed-in person whether their email is on the allowed list (reveals nothing else).
create or replace function public.job_am_i_allowed() returns boolean
language sql stable set search_path = '' as $$ select private.is_owner() $$;
revoke all on function public.job_am_i_allowed() from public, anon;
grant execute on function public.job_am_i_allowed() to authenticated;
