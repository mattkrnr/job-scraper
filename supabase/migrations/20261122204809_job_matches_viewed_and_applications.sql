alter table public.job_matches
  add column viewed_at timestamptz,
  add column applied_at date,
  add column application_url text,
  add column application_stage text check (application_stage in ('applied','interviewing','offer','rejected','no_response','withdrawn')),
  add column notes text;
comment on column public.job_matches.viewed_at is 'Set when the user first opens the job link.';
comment on column public.job_matches.application_url is 'Link to the application portal / status page for applied jobs.';
