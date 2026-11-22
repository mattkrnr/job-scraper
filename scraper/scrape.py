"""Runs every saved search, filters the results and saves new matches to Supabase.

Env:
  JOB_SCRAPER_TOKEN  secret token (GitHub secret) that unlocks the scraper functions
  SUPABASE_URL / SUPABASE_ANON_KEY  optional overrides
  DRY_RUN=1          print matches instead of saving
"""
import json
import os
import sys
import time

import requests
from jobspy import scrape_jobs

from filters import keep, to_match

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://lvaxrikegqknfkzrvmbr.supabase.co")
ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "sb_publishable_A_Mn1IHTHj8uV_e2ut0LXw_wGg8qfsA")
TOKEN = os.environ.get("JOB_SCRAPER_TOKEN", "")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
RESULTS_PER_QUERY = int(os.environ.get("RESULTS_PER_QUERY", "40"))


def rpc(name, payload):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/{name}",
        headers={"apikey": ANON_KEY, "Authorization": f"Bearer {ANON_KEY}", "Content-Type": "application/json"},
        data=json.dumps(payload, default=str),
        timeout=60,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"{name} failed: {r.status_code} {r.text[:300]}")
    return r.json() if r.text.strip() else None  # functions returning void send an empty body


def run_query(sites, term, location, distance, hours_old, remote=False):
    days = max(1, round(hours_old / 24))
    google_term = f"{term} {'remote ' if remote else ''}jobs {'' if remote else 'near ' + location} since {'yesterday' if days <= 1 else f'{days} days ago'}"
    kwargs = dict(
        site_name=sites,
        search_term=term,
        google_search_term=google_term.strip(),
        location=location,
        hours_old=hours_old,
        results_wanted=RESULTS_PER_QUERY,
        country_indeed="USA",
        description_format="markdown",
        verbose=0,
    )
    if not remote:
        kwargs["distance"] = distance
    df = scrape_jobs(**kwargs)
    return [] if df is None or df.empty else df.to_dict("records")


def run_search(search):
    terms = [t for t in (search.get("titles") or []) if t.strip()] or [search["name"]]
    sites = search.get("sites") or ["indeed", "google"]
    modes = search.get("work_modes") or []
    raw, errors = [], []
    for term in terms:
        queries = []
        if not modes or set(modes) & {"onsite", "hybrid"}:
            queries.append((search["origin"], False))
        if not modes or "remote" in modes:
            queries.append(("United States", True))
        for location, remote in queries:
            try:
                raw += run_query(sites, term, location, search["radius_miles"], search["hours_old"], remote)
            except Exception as e:  # one bad board shouldn't sink the run
                errors.append(f"{term}{' (remote)' if remote else ''}: {str(e)[:120]}")
            time.sleep(2)

    matches, rejected, seen, dropped = [], [], set(), {}
    for job in raw:
        m = to_match(job)
        if m["job_key"] in seen:
            continue
        seen.add(m["job_key"])
        ok, why = keep(m, search)
        if ok:
            matches.append(m)
        else:
            dropped[why] = dropped.get(why, 0) + 1
            rejected.append({**m, "reason": why})

    status = f"{len(raw)} scraped, {len(matches)} passed filters"
    if dropped:
        status += " (dropped: " + ", ".join(f"{k} {v}" for k, v in sorted(dropped.items())) + ")"
    if errors:
        status += " | errors: " + "; ".join(errors)
    return matches, rejected, status


SEARCH_DEFAULTS = {"name": "Test", "origin": "", "radius_miles": 25, "titles": [], "must_include": [], "exclude_words": [],
                   "min_salary": None, "include_no_salary": True, "work_modes": ["remote", "hybrid", "onsite"],
                   "experience_levels": [], "job_types": [], "hours_old": 72, "sites": ["indeed", "google"]}


def preview(m, keep_it, reason=""):
    return {**{k: m[k] for k in ("title", "company", "location", "work_mode", "experience_level", "job_type",
                                  "salary_min", "salary_max", "site", "job_url", "date_posted")},
            "description": m["description"][:600], "kept": keep_it, "reason": reason}


def run_tests():
    """Process test runs queued from the app. Results are previews only, never saved as matches."""
    if not TOKEN:
        sys.exit("JOB_SCRAPER_TOKEN is not set")
    tests = rpc("job_scraper_claim_tests", {"p_token": TOKEN})
    print(f"{len(tests)} test runs")
    for t in tests:
        search = {**SEARCH_DEFAULTS, **(t["params"] or {})}
        try:
            if not search["origin"].strip():
                raise ValueError("Origin is empty")
            matches, rejected, status = run_search(search)
            results = [preview(m, True) for m in matches][:150] + [preview(m, False, m["reason"]) for m in rejected][:150]
            rpc("job_scraper_finish_test", {"p_token": TOKEN, "p_id": t["id"], "p_results": results, "p_ok": True, "p_message": status})
            print(f"test {t['id']}: {status}")
        except Exception as e:
            rpc("job_scraper_finish_test", {"p_token": TOKEN, "p_id": t["id"], "p_results": [], "p_ok": False, "p_message": str(e)[:500]})
            print(f"test {t['id']} failed: {e}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "tests":
        return run_tests()
    if DRY_RUN and len(sys.argv) > 1:
        searches = [json.load(open(sys.argv[1]))]
    else:
        if not TOKEN:
            sys.exit("JOB_SCRAPER_TOKEN is not set")
        searches = rpc("job_scraper_searches", {"p_token": TOKEN})
    print(f"{len(searches)} active searches")
    for s in searches:
        try:
            matches, _rejected, status = run_search(s)
            status = "ok: " + status
        except Exception as e:  # keep going with the other searches
            matches, status = [], f"error: {str(e)[:300]}"
        if DRY_RUN:
            print(f"\n== {s['name']} — {status}")
            for m in matches[:25]:
                print(f"  {m['title']} @ {m['company']} [{m['work_mode']}, {m['experience_level']}] {m['salary_min']}-{m['salary_max']} {m['job_url']}")
            continue
        new = rpc("job_scraper_save", {"p_token": TOKEN, "p_search_id": s["id"], "p_jobs": matches, "p_status": status})
        print(f"{s['name']}: {new} new — {status}")


if __name__ == "__main__":
    main()
