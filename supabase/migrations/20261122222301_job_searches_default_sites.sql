-- Live checks from GitHub's servers: Indeed and LinkedIn return jobs; Google Jobs returned none; ZipRecruiter/Glassdoor blocked.
alter table public.job_searches alter column sites set default '{indeed,linkedin}';
