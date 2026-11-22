"""End-to-end: the real scrape.py, over HTTP, against a real Postgres built from the real migrations.

Run: PGHOST=... PGPORT=... PGUSER=postgres python -m unittest discover -s tests -v
"""
import json
import unittest

from harness import FRIEND, OWNER, TOKEN, build_db, drop_db, psql, q1, run_scraper, start_rest

SEARCH_COLS = "user_id, name, origin, radius_miles, titles, work_modes, min_salary, include_no_salary, experience_levels, job_types, active"


def add_search(user, name, **kw):
    v = {"origin": "08551", "radius_miles": 20, "titles": ["digital marketing"], "work_modes": ["remote", "hybrid", "onsite"],
         "min_salary": None, "include_no_salary": True, "experience_levels": [], "job_types": [], "active": True}
    v.update(kw)
    arr = lambda xs: "array[" + ",".join(f"'{x}'" for x in xs) + "]::text[]"
    sql = (f"insert into public.job_searches ({SEARCH_COLS}) values ('{user}', '{name}', '{v['origin']}', {v['radius_miles']}, "
           f"{arr(v['titles'])}, {arr(v['work_modes'])}, {v['min_salary'] or 'null'}, {str(v['include_no_salary']).lower()}, "
           f"{arr(v['experience_levels'])}, {arr(v['job_types'])}, {str(v['active']).lower()}) returning id;")
    return psql(sql)[0]


def matches(search_id):
    return q1(f"select coalesce(json_agg(m order by title), '[]') from public.job_matches m where search_id = '{search_id}';")


def search(search_id):
    return q1(f"select row_to_json(s) from public.job_searches s where id = '{search_id}';")


def queue_test(user, params, status="queued", started="null"):
    return psql(f"insert into public.job_test_runs (user_id, params, status, started_at) values "
                f"('{user}', '{json.dumps(params)}'::jsonb, '{status}', {started}) returning id;")[0]


def test_row(test_id):
    return q1(f"select row_to_json(t) from public.job_test_runs t where id = '{test_id}';")


class ScraperE2E(unittest.TestCase):
    def setUp(self):
        self.db = build_db()
        self.url, self.stop = start_rest()

    def tearDown(self):
        self.stop()
        drop_db(self.db)

    # ---------- scheduled runs ----------
    def test_scheduled_run_saves_matches_per_user_and_dedupes(self):
        a = add_search(OWNER, "Owner local", work_modes=["onsite", "hybrid"])
        b = add_search(FRIEND, "Friend remote 100k", work_modes=["remote"], min_salary=100000, include_no_salary=False)
        c = add_search(OWNER, "Paused", active=False)

        p, calls = run_scraper(self.url)
        self.assertEqual(p.returncode, 0, p.stderr)

        ma = matches(a)
        self.assertTrue(ma, "owner search saved nothing")
        self.assertTrue(all(m["user_id"] == OWNER for m in ma))
        titles = [m["title"] for m in ma]
        self.assertEqual(titles.count("Digital Marketing Specialist"), 2, "Acme job seen on 2 boards should be saved once (plus Delta)")
        self.assertNotIn(None, titles)                       # no-title job dropped
        self.assertTrue(any("☕" in t for t in titles))       # unicode / NaT date / no company row survived
        self.assertTrue(all(len(m["description"] or "") <= 4000 for m in ma))
        hourly = next(m for m in ma if m["company"] == "Beta LLC")
        self.assertEqual((float(hourly["salary_min"]), hourly["work_mode"]), (72800.0, "hybrid"))

        mb = matches(b)
        self.assertEqual([m["company"] for m in mb], ["Epsilon"], "only remote job with >=100k salary should pass")
        self.assertTrue(all(m["user_id"] == FRIEND for m in mb))

        self.assertEqual(matches(c), [])
        self.assertTrue(search(a)["last_run_status"].startswith("ok:"), search(a)["last_run_status"])
        self.assertIsNone(search(c)["last_run_at"])
        local = [c for c in calls if "distance" in c]
        remote = [c for c in calls if "distance" not in c]
        self.assertTrue(local and all(c["location"] == "08551" and c["distance"] == 20 for c in local))
        self.assertTrue(remote and all(c["location"] == "United States" for c in remote))
        self.assertEqual(len(calls), 2, "a: 1 local query; b: 1 remote query; paused search not scraped")

        n_before = len(ma)
        p, _ = run_scraper(self.url)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(matches(a)), n_before, "second run must not duplicate")
        self.assertEqual(search(a)["last_run_new"], 0)

    def test_one_bad_search_does_not_stop_the_others(self):
        bad = add_search(OWNER, "Junk data", origin="99999", work_modes=["onsite"])
        good = add_search(FRIEND, "Fine", work_modes=["onsite"])
        p, _ = run_scraper(self.url)
        self.assertNotEqual(p.returncode, 0, "run should be marked failed so GitHub shows it")
        self.assertIn("Junk data", p.stderr)
        self.assertTrue(matches(good), "the good search must still be saved")
        self.assertTrue(search(bad)["last_run_status"].startswith("error saving results"), search(bad)["last_run_status"])

    def test_user_statuses_survive_rerun(self):
        a = add_search(OWNER, "Owner")
        run_scraper(self.url)
        psql(f"update public.job_matches set status='applied', application_url='https://x.test' where search_id='{a}';")
        run_scraper(self.url)
        self.assertTrue(all(m["status"] == "applied" for m in matches(a)))

    def test_board_errors_are_recorded_not_fatal(self):
        a = add_search(OWNER, "Owner")
        p, _ = run_scraper(self.url, scenario="raise")
        self.assertEqual(p.returncode, 0, p.stderr)
        st = search(a)["last_run_status"]
        self.assertIn("errors:", st)
        self.assertIn("429", st)

    def test_empty_results(self):
        a = add_search(OWNER, "Owner")
        p, _ = run_scraper(self.url, scenario="empty")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(matches(a), [])
        self.assertIn("0 scraped", search(a)["last_run_status"])

    def test_no_searches(self):
        p, calls = run_scraper(self.url)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(calls, [])

    def test_bad_token_fails_loudly_and_writes_nothing(self):
        a = add_search(OWNER, "Owner")
        p, calls = run_scraper(self.url, token="wrong")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("bad token", p.stderr + p.stdout)
        self.assertEqual(calls, [])
        self.assertIsNone(search(a)["last_run_at"])

    def test_missing_token(self):
        p, _ = run_scraper(self.url, token=None)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("JOB_SCRAPER_TOKEN is not set", p.stderr)

    # ---------- test runs (Test button) ----------
    def test_test_run_returns_kept_and_dropped_without_saving_matches(self):
        params = {"name": "Draft", "origin": "08551", "radius_miles": 18, "titles": ["digital marketing", "marketing analyst"],
                  "work_modes": ["remote", "hybrid", "onsite"], "experience_levels": ["mid"], "job_types": ["fulltime"],
                  "hours_old": 168, "min_salary": None, "include_no_salary": True, "sites": ["indeed", "google"],
                  "must_include": [], "exclude_words": [], "active": True}
        t = queue_test(OWNER, params)
        self.assertEqual(q1(f"select public.job_scraper_pending_tests('{TOKEN}');", role="anon"), "1")

        p, calls = run_scraper(self.url, "tests")
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        row = test_row(t)
        self.assertEqual(row["status"], "done", row["message"])
        kept = [r for r in row["results"] if r["kept"]]
        dropped = [r for r in row["results"] if not r["kept"]]
        self.assertTrue(kept and dropped)
        reasons = {r["reason"] for r in dropped}
        self.assertTrue({"experience level", "job type", "no title"} <= reasons, reasons)
        self.assertTrue(all(r["experience_level"] == "mid" for r in kept))
        self.assertTrue(all(len(r["description"]) <= 600 for r in row["results"]))
        self.assertIn("passed filters", row["message"])
        self.assertEqual(len(calls), 4, "2 titles x (local + remote)")
        self.assertEqual(q1("select count(*) from public.job_matches;"), "0", "tests must not save matches")
        self.assertEqual(q1(f"select public.job_scraper_pending_tests('{TOKEN}');", role="anon"), "0")

        p, calls = run_scraper(self.url, "tests")   # nothing left to do
        self.assertEqual((p.returncode, calls), (0, []))

    def test_test_run_with_empty_origin_reports_error(self):
        t = queue_test(OWNER, {"name": "x", "origin": " ", "titles": ["x"]})
        p, calls = run_scraper(self.url, "tests")
        self.assertEqual(p.returncode, 0, p.stderr)
        row = test_row(t)
        self.assertEqual((row["status"], row["message"]), ("error", "Origin is empty"))
        self.assertEqual(calls, [])

    def test_test_run_board_errors_still_finish(self):
        t = queue_test(OWNER, {"name": "x", "origin": "08551", "titles": ["x"]})
        p, _ = run_scraper(self.url, "tests", scenario="raise")
        self.assertEqual(p.returncode, 0, p.stderr)
        row = test_row(t)
        self.assertEqual(row["status"], "done")
        self.assertIn("errors:", row["message"])

    def test_two_users_tests_processed_in_one_run(self):
        t1 = queue_test(OWNER, {"name": "a", "origin": "08551", "titles": ["x"]})
        t2 = queue_test(FRIEND, {"name": "b", "origin": "10001", "titles": ["y"], "work_modes": ["onsite"]})
        p, calls = run_scraper(self.url, "tests")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual({test_row(t1)["status"], test_row(t2)["status"]}, {"done"})
        self.assertIn("10001", [c["location"] for c in calls])

    def test_stuck_test_is_reclaimed_recent_one_is_not(self):
        stuck = queue_test(OWNER, {"name": "a", "origin": "08551", "titles": ["x"]}, "running", "now() - interval '45 minutes'")
        busy = queue_test(FRIEND, {"name": "b", "origin": "08551", "titles": ["x"]}, "running", "now() - interval '2 minutes'")
        p, _ = run_scraper(self.url, "tests")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(test_row(stuck)["status"], "done")
        self.assertEqual(test_row(busy)["status"], "running")

    def test_old_tests_are_cleaned_up(self):
        old = psql(f"insert into public.job_test_runs (user_id, params, status, requested_at) values "
                   f"('{OWNER}', '{{}}', 'done', now() - interval '8 days') returning id;")[0]
        run_scraper(self.url, "tests")
        self.assertIsNone(test_row(old))


if __name__ == "__main__":
    unittest.main()
