import math
import unittest

from filters import experience_level, keep, to_match, work_mode, yearly

SEARCH = {"must_include": [], "exclude_words": [], "work_modes": ["remote", "hybrid", "onsite"],
          "experience_levels": [], "job_types": [], "min_salary": None, "include_no_salary": True}


def raw(**kw):
    base = {"title": "Software Engineer", "company": "Acme", "location": "Princeton, NJ, US", "is_remote": False,
            "min_amount": math.nan, "max_amount": math.nan, "interval": None, "job_type": None,
            "site": "indeed", "job_url": "https://x.test/1", "description": "Build things.", "date_posted": None}
    base.update(kw)
    return base


class T(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(experience_level("Senior Software Engineer"), "senior")
        self.assertEqual(experience_level("Sr. Data Analyst"), "senior")
        self.assertEqual(experience_level("Engineering Manager"), "lead")
        self.assertEqual(experience_level("Junior Developer"), "entry")
        self.assertEqual(experience_level("Software Engineer I"), "entry")
        self.assertEqual(experience_level("Software Engineer II"), "mid")
        self.assertEqual(experience_level("Software Engineer"), "mid")
        self.assertEqual(experience_level("Internal Tools Engineer"), "mid")

    def test_missing_values(self):
        import pandas as pd
        from filters import blank
        for v in (None, "", "  ", math.nan, pd.NaT, pd.NA, float("nan")):
            self.assertTrue(blank(v), repr(v))
        for v in ("x", 0, 0.0, False, pd.Timestamp("2026-11-20")):
            self.assertFalse(blank(v), repr(v))
        self.assertIsNone(to_match(raw(date_posted=pd.NaT))["date_posted"])
        self.assertEqual(to_match(raw(date_posted=pd.Timestamp("2026-11-20 13:00")))["date_posted"], "2026-11-20")
        self.assertEqual(to_match(raw(title=None, company=pd.NA))["title"], "")

    def test_salary(self):
        self.assertEqual(yearly(50, "hourly"), 104000)
        self.assertEqual(yearly(120000, "yearly"), 120000)
        self.assertIsNone(yearly(math.nan, "yearly"))

    def test_work_mode(self):
        self.assertEqual(work_mode(raw(is_remote=True)), "remote")
        self.assertEqual(work_mode(raw(title="Engineer (Hybrid)")), "hybrid")
        self.assertEqual(work_mode(raw()), "onsite")

    def test_min_salary(self):
        s = {**SEARCH, "min_salary": 100000}
        self.assertFalse(keep(to_match(raw(min_amount=40, max_amount=45, interval="hourly")), s)[0])
        self.assertTrue(keep(to_match(raw(min_amount=90000, max_amount=130000, interval="yearly")), s)[0])
        self.assertTrue(keep(to_match(raw()), s)[0])
        self.assertFalse(keep(to_match(raw()), {**s, "include_no_salary": False})[0])

    def test_words_modes_levels_types(self):
        m = to_match(raw(title="Senior Backend Engineer", job_type="fulltime"))
        self.assertTrue(keep(m, {**SEARCH, "must_include": ["backend"]})[0])
        self.assertFalse(keep(m, {**SEARCH, "exclude_words": ["senior"]})[0])
        self.assertFalse(keep(m, {**SEARCH, "work_modes": ["remote"]})[0])
        self.assertFalse(keep(m, {**SEARCH, "experience_levels": ["entry", "mid"]})[0])
        self.assertTrue(keep(m, {**SEARCH, "job_types": ["fulltime"]})[0])
        self.assertFalse(keep(m, {**SEARCH, "job_types": ["contract"]})[0])

    def test_key_dedupes_across_sites(self):
        a = to_match(raw(site="indeed", job_url="https://a"))
        b = to_match(raw(site="google", job_url="https://b", location="Princeton, NJ"))
        self.assertEqual(a["job_key"], b["job_key"])


if __name__ == "__main__":
    unittest.main()
