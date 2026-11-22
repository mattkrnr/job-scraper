-- Minimal stand-in for the parts of Supabase the job scraper relies on, for local / CI tests only.
-- Mirrors the real project: roles, auth.uid()/auth.jwt(), private.allowed_users and private.is_owner(),
-- and Supabase's default table grants (RLS is what actually protects data).
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticator') then
    create role authenticator login noinherit password 'authenticator';
  end if;
end $$;
grant anon, authenticated to authenticator;

create schema if not exists auth;
create table if not exists auth.users (id uuid primary key, email text);
grant usage on schema auth to anon, authenticated;

create or replace function auth.jwt() returns jsonb language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claim', true), ''),
                  nullif(current_setting('request.jwt.claims', true), ''))::jsonb
$$;
create or replace function auth.uid() returns uuid language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
                  (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'))::uuid
$$;

create schema if not exists private;
create table if not exists private.allowed_users (email text not null);
grant usage on schema private to authenticated;        -- same as the real project (anon has none)
grant select on private.allowed_users to authenticated;  -- same as the real project (schema is not exposed over the API)

-- Same definition as production (owner = Matt's user id)
create or replace function private.is_owner() returns boolean language sql stable set search_path = '' as $$
  select (select auth.uid()) = '2f388ba8-e793-41fd-a664-a582aeb33655'::uuid
      or exists (select 1 from private.allowed_users a where a.email = lower((select auth.jwt()) ->> 'email'))
$$;
grant execute on function private.is_owner() to anon, authenticated;

grant usage on schema public to anon, authenticated;
alter default privileges in schema public grant all on tables to anon, authenticated;
alter default privileges in schema public grant all on functions to anon, authenticated;
