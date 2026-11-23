"""Browser tests: the real index.html + real supabase-js in Chromium, talking to a real PostgREST over the
same database schema. Runs in CI (needs network for supabase-js and a PostgREST service).

Env: APP_URL (static server for the repo root), REAL_POSTGREST_URL, UI_JWT_SECRET, PG* for the `app` database.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness import FRIEND, OWNER, STRANGER, EMAILS, psql, q1  # noqa: E402

from playwright.sync_api import expect, sync_playwright  # noqa: E402

APP = os.environ.get("APP_URL", "http://localhost:8000/index.html")
REST = os.environ.get("REAL_POSTGREST_URL", "http://localhost:3000")
SECRET = os.environ.get("UI_JWT_SECRET", "")
SB = "https://lvaxrikegqknfkzrvmbr.supabase.co"
STORAGE_KEY = "sb-lvaxrikegqknfkzrvmbr-auth-token"


def b64(d):
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


def jwt(user):
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"sub": user, "email": EMAILS[user], "role": "authenticated", "aud": "authenticated",
                           "exp": int(time.time()) + 3600 * 24}).encode())
    sig = b64(hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def session(user):
    return {"access_token": jwt(user), "refresh_token": "test-refresh", "token_type": "bearer", "expires_in": 86400,
            "expires_at": int(time.time()) + 86400,
            "user": {"id": user, "email": EMAILS[user], "aud": "authenticated", "role": "authenticated",
                     "app_metadata": {"provider": "email"}, "user_metadata": {}, "created_at": "2026-11-01T00:00:00Z"}}


@unittest.skipUnless(SECRET, "UI tests run in CI (set UI_JWT_SECRET, REAL_POSTGREST_URL, APP_URL)")
class AppUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        psql("delete from public.job_test_runs; delete from public.job_matches; delete from public.job_searches;")
        self.errors, self.otp_calls = [], []

    def open_as(self, user):
        ctx = self.browser.new_context()
        if user:
            ctx.add_init_script(f"localStorage.setItem({json.dumps(STORAGE_KEY)}, {json.dumps(json.dumps(session(user)))});")

        def rest(route):
            req = route.request
            target = REST + req.url[len(SB + "/rest/v1"):]
            try:
                resp = route.fetch(url=target)
                body = resp.body()  # read now: the response object can be disposed if the page moves on
                route.fulfill(status=resp.status, body=body,
                              headers={**resp.headers, "access-control-allow-origin": "*",
                                       "access-control-expose-headers": "content-range"})
            except Exception as e:  # page navigated away mid-request (reload); nothing to deliver
                if "disposed" not in str(e) and "closed" not in str(e):
                    raise

        def auth(route):
            url = route.request.url
            if "/otp" in url:
                self.otp_calls.append(route.request.post_data_json)
                return route.fulfill(status=200, json={}, headers={"access-control-allow-origin": "*"})
            if "/logout" in url:
                return route.fulfill(status=204, headers={"access-control-allow-origin": "*"})
            if user and "/user" in url:
                return route.fulfill(status=200, json=session(user)["user"], headers={"access-control-allow-origin": "*"})
            return route.fulfill(status=200, json=session(user) if user else {}, headers={"access-control-allow-origin": "*"})

        ctx.route(SB + "/rest/v1/**", rest)
        ctx.route(SB + "/auth/v1/**", auth)
        page = ctx.new_page()
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and "favicon" not in m.text and self.errors.append(m.text))
        page.on("dialog", lambda d: d.accept())
        page.goto(APP)
        self.addCleanup(ctx.close)
        return page

    def tearDown(self):
        self.assertEqual(self.errors, [], "browser console errors")

    # ---------- helpers ----------
    def create_search(self, page, name, origin="08551", titles="digital marketing, marketing analyst", levels=("mid",)):
        page.click("#newBtn")
        page.fill("#f_name", name)
        page.fill("#f_origin", origin)
        page.fill("#f_radius", "18")
        page.fill("#f_titles", titles)
        for lv in levels:
            page.click(f"label:has(input[name=levels][value={lv}])")
        page.click("label:has(input[name=types][value=fulltime])")
        page.click("#editForm button[type=submit]")
        expect(page.locator(".search-item", has_text=name)).to_be_visible()

    def add_match(self, user, search_id, key, title, **kw):
        cols = {"search_id": search_id, "user_id": user, "job_key": key, "title": title, "company": "Acme",
                "location": "Princeton, NJ", "work_mode": "onsite", "experience_level": "mid", "site": "indeed",
                "job_url": "https://example.com/job", "salary_min": 70000, "salary_max": 90000}
        cols.update(kw)
        names = ", ".join(cols)
        vals = ", ".join("null" if v is None else f"'{str(v).replace(chr(39), chr(39) * 2)}'" for v in cols.values())
        psql(f"insert into public.job_matches ({names}) values ({vals});")

    # ---------- tests ----------
    def test_signed_out_shows_login_and_sends_link(self):
        page = self.open_as(None)
        expect(page.locator("#login")).to_be_visible()
        expect(page.locator("#app")).to_be_hidden()   # the app area must not show behind the sign-in box
        page.fill("#email", "friend@example.com")
        page.click("#loginForm button")
        expect(page.locator("#loginMsg")).to_contain_text("Check your email")
        self.assertEqual(self.otp_calls[0]["email"], "friend@example.com")

    def test_create_edit_duplicate_delete(self):
        page = self.open_as(OWNER)
        expect(page.locator("#searchList")).to_contain_text("No searches yet")
        self.create_search(page, "Digital Marketing")
        row = q1("select row_to_json(s) from public.job_searches s where name='Digital Marketing';")
        self.assertEqual(row["user_id"], OWNER)
        self.assertEqual(row["titles"], ["digital marketing", "marketing analyst"])
        self.assertEqual((row["origin"], row["radius_miles"]), ("08551", 18))
        self.assertEqual((row["experience_levels"], row["job_types"]), (["mid"], ["fulltime"]))
        self.assertEqual(sorted(row["work_modes"]), ["hybrid", "onsite", "remote"])
        self.assertEqual(row["sites"], ["indeed", "linkedin"])

        page.fill("#f_radius", "30")
        page.click("#editForm button[type=submit]")
        expect(page.locator("#toast")).to_contain_text("Saved")
        self.assertEqual(q1("select radius_miles from public.job_searches where name='Digital Marketing';"), "30")

        page.click("#dupBtn")
        expect(page.locator("#f_name")).to_have_value("Digital Marketing (copy)")
        expect(page.locator("#f_titles")).to_have_value("digital marketing, marketing analyst")
        page.fill("#f_origin", "10001")
        page.click("#editForm button[type=submit]")
        expect(page.locator(".search-item")).to_have_count(2)
        self.assertEqual(q1("select origin from public.job_searches where name='Digital Marketing';"), "08551")
        self.assertEqual(q1("select origin from public.job_searches where name='Digital Marketing (copy)';"), "10001")

        page.click("#delBtn")
        expect(page.locator(".search-item")).to_have_count(1)
        self.assertEqual(q1("select count(*) from public.job_searches;"), "1")

    def test_clicking_a_section_title_does_not_toggle_a_filter(self):
        page = self.open_as(OWNER)
        page.click("#newBtn")
        for title in ("Work setup", "Experience level", "Job type", "Job boards"):
            page.locator(".field > span", has_text=title).click()
        checked = page.evaluate("""() => Object.fromEntries(['modes','levels','types','sites'].map(n =>
            [n, [...document.querySelectorAll(`input[name=${n}]:checked`)].map(i => i.value)]))""")
        self.assertEqual(checked, {"modes": ["remote", "hybrid", "onsite"], "levels": [], "types": [],
                                   "sites": ["indeed", "linkedin"]})

    def test_validation(self):
        page = self.open_as(OWNER)
        page.click("#newBtn")
        page.fill("#f_name", "No modes")
        page.fill("#f_origin", "08551")
        for m in ("remote", "hybrid", "onsite"):
            page.click(f"label:has(input[name=modes][value={m}])")
        page.click("#editForm button[type=submit]")
        expect(page.locator("#toast")).to_contain_text("work setup")
        self.assertEqual(q1("select count(*) from public.job_searches;"), "0")

    def test_test_button_queue_replace_cancel_and_results(self):
        page = self.open_as(OWNER)
        self.create_search(page, "DM")
        page.fill("#f_origin", "19103")   # unsaved edit should be what gets tested
        page.click("#testBtn")
        expect(page.locator("#testPanel")).to_contain_text("Waiting for GitHub")
        self.assertEqual(q1("select params->>'origin' from public.job_test_runs;"), "19103")

        page.fill("#f_origin", "10001")
        page.click("#testBtn")
        expect(page.locator("#toast")).to_contain_text("Replaced")
        self.assertEqual(q1("select count(*) || ':' || max(params->>'origin') from public.job_test_runs;"), "1:10001")

        page.click("#cancelTest")
        expect(page.locator("#testPanel")).to_be_hidden()
        self.assertEqual(q1("select count(*) from public.job_test_runs;"), "0")

        sid = q1("select id from public.job_searches where name='DM';")
        results = [{"title": "Kept Job", "company": "A", "location": "X", "work_mode": "onsite", "experience_level": "mid",
                    "site": "indeed", "job_url": "https://example.com/1", "kept": True, "reason": ""},
                   {"title": "Senior Thing", "company": "B", "location": "Y", "work_mode": "onsite", "experience_level": "senior",
                    "site": "indeed", "job_url": "https://example.com/2", "kept": False, "reason": "experience level"}]
        psql(f"insert into public.job_test_runs (user_id, search_id, params, status, finished_at, message, results) values "
             f"('{OWNER}', '{sid}', '{{}}', 'done', now(), '2 scraped, 1 passed filters', '{json.dumps(results)}');")
        page.reload()
        page.click(".search-item")
        expect(page.locator("#testPanel")).to_contain_text("Would match (1)")
        expect(page.locator("#testPanel")).to_contain_text("Filtered out (1)")
        expect(page.locator("#testPanel")).to_contain_text("experience level: 1")

    def test_matches_statuses_and_application_tracking(self):
        page = self.open_as(OWNER)
        self.create_search(page, "DM")
        sid = q1("select id from public.job_searches where name='DM';")
        self.add_match(OWNER, sid, "a", "Job A")
        self.add_match(OWNER, sid, "b", "Job B")
        self.add_match(OWNER, sid, "x", '<img src=x onerror="window.__xss=1">Evil', company="<b>bold</b>")
        page.reload()
        page.click(".search-item")
        expect(page.locator(".search-item .badge")).to_have_text("3")
        expect(page.locator("#matches .job")).to_have_count(3)
        self.assertIsNone(page.evaluate("window.__xss"), "job text must not run as HTML")
        expect(page.locator("#matches")).to_contain_text("<b>bold</b>")

        job_a = page.locator("#matches .job", has_text="Job A")
        job_a.locator("[data-view]").click()
        self.assertIsNotNone(json.loads(json.dumps(q1("select viewed_at from public.job_matches where job_key='a';"))))
        expect(page.locator("#matches .job", has_text="Job A")).to_contain_text("Viewed")

        page.locator("#matches .job", has_text="Job B").locator("[data-set=saved]").click()
        expect(page.locator("[data-tab=saved]")).to_contain_text("1")
        self.assertEqual(q1("select status from public.job_matches where job_key='b';"), "saved")

        page.locator("#matches .job", has_text="Job A").locator("[data-set=applied]").click()
        expect(page.locator("[data-tab=applied]")).to_have_class("small on")
        box = page.locator("[data-app]")
        expect(box).to_be_visible()
        box.locator("[data-f=application_url]").fill("myworkday.com/acme/status")
        box.locator("[data-f=application_stage]").select_option("interviewing")
        box.locator("[data-f=notes]").fill("Recruiter: Pat")
        box.locator("[data-saveapp]").click()
        expect(page.locator("#toast")).to_contain_text("Application saved")
        row = q1("select row_to_json(m) from public.job_matches m where job_key='a';")
        self.assertEqual((row["status"], row["application_stage"], row["notes"]), ("applied", "interviewing", "Recruiter: Pat"))
        self.assertEqual(row["application_url"], "https://myworkday.com/acme/status")
        self.assertIsNotNone(row["applied_at"])

        page.click("[data-tab=new]")
        page.click("#clearNew")
        self.assertEqual(q1("select status from public.job_matches where job_key='x';"), "dismissed")
        expect(page.locator(".search-item .badge")).to_have_count(0)

    def test_match_filters(self):
        page = self.open_as(OWNER)
        self.create_search(page, "DM")
        sid = q1("select id from public.job_searches where name='DM';")
        self.add_match(OWNER, sid, "a", "SEO Specialist", company="Acme", work_mode="remote", experience_level="mid",
                       salary_min=60000, salary_max=80000, date_posted="2020-01-01")
        self.add_match(OWNER, sid, "b", "Growth Marketer", company="Beta", work_mode="onsite", experience_level="senior",
                       salary_min=120000, salary_max=140000)
        self.add_match(OWNER, sid, "c", "Content Lead", company="Gamma", work_mode="hybrid", experience_level="lead",
                       salary_min=None, salary_max=None)
        page.reload()
        page.click(".search-item")
        jobs = page.locator("#matchList .job")
        expect(jobs).to_have_count(3)

        page.locator("#mf_q").press_sequentially("beta")       # list updates while typing; box keeps focus
        expect(jobs).to_have_count(1)
        expect(jobs).to_contain_text("Growth Marketer")
        expect(page.locator("#mf_q")).to_be_focused()
        expect(page.locator("#mf_summary")).to_contain_text("Showing 1 of 3")
        page.fill("#mf_q", "")

        page.click("#matches label:has(input[data-mf=modes][value=remote])")
        expect(jobs).to_have_count(1)
        page.click("#matches label:has(input[data-mf=modes][value=hybrid])")
        expect(jobs).to_have_count(2)
        page.click("#mf_clear")
        expect(jobs).to_have_count(3)

        page.click("#matches label:has(input[data-mf=levels][value=senior])")
        expect(jobs).to_have_count(1)
        page.click("#mf_clear")

        page.fill("#mf_min", "100000")
        expect(jobs).to_have_count(2)                            # Beta (140k) + Gamma (no salary listed)
        page.click("#matches label:has(input[data-mf=hideNoSalary])")
        expect(jobs).to_have_count(1)
        page.click("#mf_clear")

        page.select_option("#mf_days", "7")                      # Acme was posted in 2020
        expect(jobs).to_have_count(2)
        page.click("#mf_clear")

        page.select_option("#mf_sort", "salary")
        expect(jobs.nth(0)).to_contain_text("Growth Marketer")
        expect(jobs.nth(2)).to_contain_text("Content Lead")      # no salary sorts last
        page.select_option("#mf_sort", "company")
        expect(jobs.nth(0)).to_contain_text("Acme")

        page.click("#matches label:has(input[data-mf=modes][value=onsite])")
        expect(page.locator("#clearNew")).to_contain_text("Dismiss 1 shown new")
        page.click("#clearNew")
        self.assertEqual(q1("select string_agg(job_key || '=' || status, ',' order by job_key) from public.job_matches;"),
                         "a=new,b=dismissed,c=new", "only the filtered (visible) new job is dismissed")

        page.click("#matches label:has(input[data-mf=modes][value=onsite])")
        page.locator("#matchList .job", has_text="SEO Specialist").locator("[data-set=saved]").click()
        expect(page.locator("[data-tab=saved]")).to_contain_text("1")

    def test_users_only_see_their_own_searches_in_the_app(self):
        page = self.open_as(OWNER)
        self.create_search(page, "Owner private search")
        friend = self.open_as(FRIEND)
        expect(friend.locator("#searchList")).to_contain_text("No searches yet")
        self.create_search(friend, "Friend search")
        page.reload()
        expect(page.locator(".search-item")).to_have_count(1)
        self.assertEqual(q1("select user_id from public.job_searches where name='Friend search';"), FRIEND)

    def test_stranger_gets_a_clear_message(self):
        page = self.open_as(STRANGER)
        expect(page.locator("#notAllowed")).to_be_visible()
        expect(page.locator("#notAllowed")).to_contain_text("stranger@example.com")
        expect(page.locator("#app")).to_be_hidden()

    def test_allowed_friend_gets_the_app(self):
        page = self.open_as(FRIEND)
        expect(page.locator("#app")).to_be_visible()
        expect(page.locator("#notAllowed")).to_be_hidden()


if __name__ == "__main__":
    unittest.main()
