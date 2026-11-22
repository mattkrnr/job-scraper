"""Checks that the local PostgREST stand-in (rpc_shim.py) answers exactly like real PostgREST for every
scraper function. Runs in CI where real PostgREST is available (REAL_POSTGREST_URL); skipped locally.
This is the class of bug that broke the first live test: a `void` function returns an empty body.
"""
import json
import os
import unittest
import urllib.request

from harness import TOKEN, psql, start_rest

REAL = os.environ.get("REAL_POSTGREST_URL")  # e.g. http://localhost:3000 (no /rest/v1 prefix)


def call(base, fn, args):
    url = f"{base}/rpc/{fn}" if base == REAL else f"{base}/rest/v1/rpc/{fn}"
    req = urllib.request.Request(url, data=json.dumps(args).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


@unittest.skipUnless(REAL, "set REAL_POSTGREST_URL to compare with real PostgREST")
class RestParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The real PostgREST is pointed at this database by the CI workflow (PGDATABASE=app, built beforehand).
        cls.shim, cls.stop = start_rest()

    @classmethod
    def tearDownClass(cls):
        cls.stop()

    def both(self, fn, args):
        return call(REAL, fn, args), call(self.shim, fn, args)

    def test_scalar(self):
        (rs, rb), (ss, sb) = self.both("job_scraper_pending_tests", {"p_token": TOKEN})
        self.assertEqual((rs, json.loads(rb)), (ss, json.loads(sb)))

    def test_setof(self):
        (rs, rb), (ss, sb) = self.both("job_scraper_searches", {"p_token": TOKEN})
        self.assertEqual(rs, ss)
        self.assertIsInstance(json.loads(rb), list)
        self.assertIsInstance(json.loads(sb), list)

    def test_void_returns_empty_body(self):
        tid = psql("insert into public.job_test_runs (user_id, params) values "
                   "('2f388ba8-e793-41fd-a664-a582aeb33655', '{}') returning id;")[0]
        args = {"p_token": TOKEN, "p_id": tid, "p_results": [], "p_ok": True, "p_message": "x"}
        (rs, rb), (ss, sb) = self.both("job_scraper_finish_test", args)
        psql(f"delete from public.job_test_runs where id = '{tid}';")
        self.assertEqual(rb.strip(), "", f"real PostgREST void body: {rb!r} status {rs}")
        self.assertEqual((rs, rb.strip()), (ss, sb.strip()))

    def test_bad_token_is_an_error_in_both(self):
        (rs, _), (ss, _) = self.both("job_scraper_pending_tests", {"p_token": "nope"})
        self.assertGreaterEqual(rs, 400)
        self.assertGreaterEqual(ss, 400)


if __name__ == "__main__":
    unittest.main()
