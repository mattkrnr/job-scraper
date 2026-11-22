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

## Tests

Every push runs **Actions → Tests**:

| Suite | What it proves |
| --- | --- |
| `scraper/test_filters.py` | Filter rules, salary conversion, missing values (NaN/NaT) |
| `tests/test_scraper_e2e.py` | The real `scrape.py` over HTTP against Postgres built from `supabase/migrations` (fake job boards): scheduled runs, Test button runs, dedupe, bad data, board errors, bad/missing token |
| `tests/test_permissions.py` | Row-level security as owner / allowed friend / stranger / anonymous |
| `tests/test_rest_parity.py` | The local API stand-in answers exactly like real PostgREST |
| `tests/ui/test_app_ui.py` | Real page + real supabase-js in Chromium: create/edit/duplicate/delete, Test button, matches, applications, isolation |
| `live-boards` job | Which job boards answer GitHub's servers (informational) |

Run locally: `PGHOST=… PGUSER=postgres python -m unittest discover -s tests` (needs Postgres 16 and `psql`).

## Roadmap

- Phase 2: email alerts for new matches (`job_matches.notified_at` is ready), commute time.
- Phase 3: resume vs job → Great Fit / Good Fit.
