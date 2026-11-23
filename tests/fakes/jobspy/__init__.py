"""Fake JobSpy for tests: returns realistic rows (same columns/types as python-jobspy) without network.

FAKE_JOBSPY_SCENARIO: normal (default) | empty | raise
FAKE_JOBSPY_LOG: file to append each call's kwargs to (JSON lines)
"""
import datetime as dt
import json
import math
import os

import pandas as pd

NAN = math.nan
COLUMNS = ["id", "site", "job_url", "job_url_direct", "title", "company", "location", "date_posted", "job_type",
           "salary_source", "interval", "min_amount", "max_amount", "currency", "is_remote", "job_level",
           "description", "company_industry"]


def row(**kw):
    base = {c: None for c in COLUMNS}
    base.update({"id": "x", "site": "indeed", "job_url": "https://www.indeed.com/viewjob?jk=1",
                 "title": "Digital Marketing Specialist", "company": "Acme Corp", "location": "Princeton, NJ, US",
                 "date_posted": dt.date(2026, 11, 20), "job_type": "fulltime", "interval": "yearly",
                 "min_amount": 70000.0, "max_amount": 90000.0, "currency": "USD", "is_remote": False,
                 "description": "Run campaigns."})
    base.update(kw)
    return base


LOCAL = [
    row(id="1"),                                                                   # mid, onsite, 70-90k
    row(id="2", site="linkedin", job_url="https://linkedin.com/jobs/1", location="Princeton, NJ"),  # same job on 2 boards -> dedupe
    row(id="3", title="Senior Digital Marketing Manager", min_amount=120000.0, max_amount=150000.0),  # lead (manager)
    row(id="4", title="Marketing Analyst (Hybrid)", company="Beta LLC", interval="hourly", min_amount=35.0, max_amount=45.0),
    row(id="5", title="Marketing Intern", company="Gamma", job_type="internship", min_amount=NAN, max_amount=NAN, interval=None),
    row(id="6", title=None, company="No Title Inc"),                                 # dropped: no title
    row(id="7", title="Coordinator – Marketing ☕", company=None, date_posted=pd.NaT,  # NaT date, no company, unicode
        min_amount=NAN, max_amount=NAN, interval=None, job_type=None, description="x" * 20000),
    row(id="8", title="Digital Marketing Specialist", company="Delta", job_type="parttime, contract", is_remote=None),
]
REMOTE = [
    row(id="r1", title="Remote Digital Marketing Specialist", company="Epsilon", location="Remote", is_remote=True,
        min_amount=110000.0, max_amount=130000.0),
    row(id="r2", title="Digital Marketing Coordinator", company="Zeta", location="United States", is_remote=True,
        min_amount=NAN, max_amount=NAN, interval=None),
    # the nationwide query also returns on-site / hybrid jobs far away; these must never be kept
    row(id="r3", title="Digital Marketing Specialist", company="Far Away Co", location="Austin, TX, USA", is_remote=False),
    row(id="r4", title="Digital Marketing Analyst (Hybrid)", company="Also Far", location="Honolulu, HI, USA", is_remote=False),
]


def scrape_jobs(**kwargs):
    log = os.environ.get("FAKE_JOBSPY_LOG")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps({k: v for k, v in kwargs.items()}, default=str) + "\n")
    scenario = os.environ.get("FAKE_JOBSPY_SCENARIO", "normal")
    if scenario == "raise":
        raise RuntimeError("HTTP 429 blocked by board")
    if scenario == "empty":
        return pd.DataFrame(columns=COLUMNS)
    rows = REMOTE if "distance" not in kwargs else LOCAL
    if kwargs.get("location") == "99999":  # a board returning junk the database will reject
        rows = [row(id="bad", date_posted="sometime last week")]
    rows = [r for r in rows if r["site"] in kwargs.get("site_name", ["indeed", "linkedin"])]
    return pd.DataFrame(rows, columns=COLUMNS)
