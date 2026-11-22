-- Test users and the scraper token used by the tests.
insert into auth.users (id, email) values
  ('2f388ba8-e793-41fd-a664-a582aeb33655', 'owner@example.com'),   -- the owner id hard-coded in is_owner()
  ('11111111-1111-1111-1111-111111111111', 'friend@example.com'),  -- on the allowed list
  ('22222222-2222-2222-2222-222222222222', 'stranger@example.com') -- has an account, NOT allowed
on conflict do nothing;
insert into private.allowed_users (email) values ('friend@example.com');
insert into public.job_scraper_tokens (token_hash) values (sha256(convert_to('test-token', 'UTF8')));
