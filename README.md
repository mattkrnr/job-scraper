# Job Scraper

Saved job searches with filters. A GitHub Action checks Indeed / Google Jobs (and optionally LinkedIn, ZipRecruiter, Glassdoor) every 6 hours using [JobSpy](https://github.com/speedyapply/JobSpy) and saves new matches to Supabase. The web app (GitHub Pages) is where you create, edit and duplicate searches and review matches.

## Pieces

| Part | Where |
| --- | --- |
| Web app | `index.html` → https://mattkrnr.github.io/job-scraper/ |
| Scraper | `scraper/scrape.py` (filters in `scraper/filters.py`) |
| Schedule | `.github/workflows/scrape.yml` — every 6 hours, or **Actions → Scrape jobs → Run workflow** |
| Data | Supabase project "Personal": `job_searches`, `job_matches`, `job_scraper_tokens` |

## Search fields

Name, origin (ZIP or address — per search, not per user), distance in miles, job titles (each searched separately), must-include / skip words for the title, minimum yearly salary (hourly × 2080) and whether to keep jobs without a salary, work setup (remote / hybrid / on-site), experience level, job type, posted within, job boards, active/paused.

Experience level and hybrid are **guessed** from the title/location/description — job boards don't give them reliably.

## Access

Sign in by email link. Only the owner and emails in `private.allowed_users` can use it (same rule as the other personal apps); each person only sees their own searches.

## Setup

1. Repo secret `JOB_SCRAPER_TOKEN` (Settings → Secrets and variables → Actions).
2. Settings → Pages → Deploy from branch → `main` / root.
3. Supabase → Authentication → URL Configuration: add `https://mattkrnr.github.io/job-scraper/` to Redirect URLs.

Local dry run (no saving): `cd scraper && pip install -r requirements.txt && DRY_RUN=1 python scrape.py search.json`

## Roadmap

- Phase 2: email alerts for new matches (`job_matches.notified_at` is ready), commute time.
- Phase 3: resume vs job → Great Fit / Good Fit.
