"""Tiny live check: does each job board return anything to this machine? Prints a table; never fails."""
from jobspy import scrape_jobs

CHECKS = [
    ("indeed", {}),
    ("zip_recruiter", {}),
    ("linkedin", {}),
    ("glassdoor", {}),
    ("google", {"google_search_term": "marketing jobs near Princeton, NJ since yesterday"}),
    ("google", {"google_search_term": "marketing jobs near 08551 since 7 days ago"}),
    ("google", {"google_search_term": "marketing jobs near Princeton, NJ"}),
]

for site, extra in CHECKS:
    try:
        df = scrape_jobs(site_name=[site], search_term="marketing", location="Princeton, NJ", distance=25,
                         hours_old=168, results_wanted=5, country_indeed="USA", verbose=0, **extra)
        n = 0 if df is None else len(df)
        print(f"{site:14} {n:3} jobs  {extra.get('google_search_term', '')}")
    except Exception as e:
        print(f"{site:14} ERROR {str(e)[:150]}  {extra.get('google_search_term', '')}")
