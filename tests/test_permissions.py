"""Row-level security: each person sees only their own data; strangers and anonymous visitors see nothing.

Every query runs exactly as the web app's requests do: role `authenticated` with that user's JWT claims
(or role `anon` with no sign-in). Also mirrors the app's own write patterns (create, duplicate, test queue/replace/cancel,
status and application updates).
"""
import unittest

from harness import FRIEND, OWNER, STRANGER, build_db, drop_db, psql, q1


def as_user(user, sql, check=True):
    return psql(sql, role="authenticated", user=user, check=check)


def count(user, table):
    return int(as_user(user, f"select count(*) from public.{table};")[0])


def fails(user, sql, role="authenticated"):
    p = psql(sql, role=role, user=user if role == "authenticated" else None, check=False)
    return p.returncode != 0, p.stderr


class Permissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = build_db()
        # app-style inserts: no user_id sent, it comes from the sign-in
        cls.owner_search = as_user(OWNER, "insert into public.job_searches (name, origin) values ('Mine', '08551') returning id;")[0]
        cls.friend_search = as_user(FRIEND, "insert into public.job_searches (name, origin) values ('Theirs', '10001') returning id;")[0]
        for uid, sid in ((OWNER, cls.owner_search), (FRIEND, cls.friend_search)):
            psql(f"insert into public.job_matches (search_id, user_id, job_key, title) values ('{sid}', '{uid}', 'k1', 'Job');")

    @classmethod
    def tearDownClass(cls):
        drop_db(cls.db)

    def test_user_id_comes_from_sign_in(self):
        self.assertEqual(q1(f"select user_id from public.job_searches where id='{self.friend_search}';"), FRIEND)

    def test_each_user_sees_only_their_own(self):
        for user in (OWNER, FRIEND):
            for table in ("job_searches", "job_matches"):
                self.assertEqual(count(user, table), 1, f"{user} {table}")
        self.assertEqual(as_user(FRIEND, "select name from public.job_searches;"), ["Theirs"])

    def test_stranger_with_account_sees_and_writes_nothing(self):
        for table in ("job_searches", "job_matches", "job_test_runs"):
            self.assertEqual(count(STRANGER, table), 0)
        bad, err = fails(STRANGER, "insert into public.job_searches (name, origin) values ('x', '1');")
        self.assertTrue(bad, "stranger must not create searches")
        self.assertIn("row-level security", err)

    def test_anonymous_sees_and_writes_nothing(self):
        self.assertEqual(psql("select count(*) from public.job_searches;", role="anon"), ["0"])
        self.assertEqual(psql("select count(*) from public.job_matches;", role="anon"), ["0"])
        bad, _ = fails(None, "insert into public.job_searches (name, origin, user_id) values ('x','1','%s');" % OWNER, role="anon")
        self.assertTrue(bad)

    def test_cannot_write_into_someone_elses_account(self):
        bad, _ = fails(FRIEND, f"insert into public.job_searches (user_id, name, origin) values ('{OWNER}', 'x', '1');")
        self.assertTrue(bad, "friend must not create a search owned by the owner")
        bad, _ = fails(FRIEND, f"update public.job_searches set user_id = '{FRIEND}' where id = '{self.owner_search}' returning id;")
        # update of an invisible row affects 0 rows rather than erroring; make sure nothing changed
        self.assertEqual(q1(f"select user_id from public.job_searches where id='{self.owner_search}';"), OWNER)
        as_user(FRIEND, f"update public.job_matches set status='dismissed' where search_id='{self.owner_search}';")
        as_user(FRIEND, f"delete from public.job_searches where id='{self.owner_search}';")
        self.assertEqual(q1(f"select status from public.job_matches where search_id='{self.owner_search}';"), "new")
        bad, _ = fails(FRIEND, f"update public.job_searches set user_id = '{OWNER}' where id = '{self.friend_search}';")
        self.assertTrue(bad, "friend must not hand their search to someone else")

    def test_app_status_and_application_updates(self):
        as_user(FRIEND, f"update public.job_matches set status='applied', applied_at='2026-11-22', application_stage='interviewing', "
                        f"application_url='https://jobs.test/status', notes='call Fri', viewed_at=now() where search_id='{self.friend_search}';")
        self.assertEqual(q1(f"select application_stage from public.job_matches where search_id='{self.friend_search}';"), "interviewing")
        bad, _ = fails(FRIEND, f"update public.job_matches set application_stage='ghosted' where search_id='{self.friend_search}';")
        self.assertTrue(bad, "unknown stage must be rejected")
        bad, _ = fails(FRIEND, f"update public.job_matches set status='maybe' where search_id='{self.friend_search}';")
        self.assertTrue(bad)

    def test_duplicate_search_pattern(self):
        sid = as_user(OWNER, "insert into public.job_searches (name, origin, titles, radius_miles) "
                             "values ('Mine (copy)', '10001', array['a','b'], 40) returning id;")[0]
        self.assertEqual(count(OWNER, "job_searches"), 2)
        as_user(OWNER, f"delete from public.job_searches where id='{sid}';")

    def test_test_queue_replace_cancel(self):
        t = as_user(FRIEND, "insert into public.job_test_runs (params) values ('{\"origin\":\"1\"}') returning id;")[0]
        bad, err = fails(FRIEND, "insert into public.job_test_runs (params) values ('{}');")
        self.assertTrue(bad and "job_test_runs_one_open_per_user" in err, "second open test must hit the unique index (app shows 'replace')")
        as_user(OWNER, "insert into public.job_test_runs (params) values ('{}');")  # other users are unaffected
        as_user(FRIEND, f"update public.job_test_runs set params='{{\"origin\":\"2\"}}', requested_at=now() where id='{t}' and status='queued';")
        self.assertEqual(q1(f"select params->>'origin' from public.job_test_runs where id='{t}';"), "2")
        self.assertEqual(count(FRIEND, "job_test_runs"), 1, "friend must not see owner's test")
        as_user(FRIEND, f"delete from public.job_test_runs where id='{t}' and status='queued';")
        self.assertEqual(count(FRIEND, "job_test_runs"), 0)
        psql("delete from public.job_test_runs;")

    def test_signed_in_users_cannot_call_scraper_functions_or_read_tokens(self):
        for fn in ("job_scraper_searches('test-token')", "job_scraper_pending_tests('test-token')",
                   "job_scraper_claim_tests('test-token')"):
            bad, err = fails(FRIEND, f"select * from public.{fn};")
            self.assertTrue(bad and "permission denied" in err, fn)
        self.assertEqual(count(FRIEND, "job_scraper_tokens"), 0)

    def test_am_i_allowed(self):
        self.assertEqual(as_user(OWNER, "select public.job_am_i_allowed();"), ["t"])
        self.assertEqual(as_user(FRIEND, "select public.job_am_i_allowed();"), ["t"])
        self.assertEqual(as_user(STRANGER, "select public.job_am_i_allowed();"), ["f"])
        bad, _ = fails(None, "select public.job_am_i_allowed();", role="anon")
        self.assertTrue(bad, "anonymous callers get an error, not an answer")

    def test_scraper_functions_need_the_right_token(self):
        for fn in ("job_scraper_searches('nope')", "job_scraper_pending_tests('nope')", "job_scraper_claim_tests(null)"):
            bad, err = fails(None, f"select * from public.{fn};", role="anon")
            self.assertTrue(bad and "bad token" in err, fn)
        self.assertEqual(psql("select count(*) from public.job_scraper_searches('test-token');", role="anon"), ["2"])


if __name__ == "__main__":
    unittest.main()
