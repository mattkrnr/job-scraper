"""Pure filtering helpers for the job scraper (no network, easy to test)."""
import math
import re

YEARLY_FACTOR = {"yearly": 1, "monthly": 12, "weekly": 52, "daily": 260, "hourly": 2080}

SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|iii|iv)\b", re.I)
LEAD = re.compile(r"\b(lead|manager|director|head of|vp|vice president|architect)\b", re.I)
ENTRY = re.compile(r"(?i:\b(junior|jr\.?|entry[- ]level|new grad|graduate|apprentice|trainee|intern|internship)\b)|\s[Ii]\s*$")


def blank(v):
    """True for None, empty strings and pandas/numpy missing values (NaN, NaT, pd.NA)."""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    try:
        return bool(v != v)  # NaN and NaT are the only values not equal to themselves
    except (TypeError, ValueError):  # pd.NA raises on bool()
        return True


def text(v):
    return "" if blank(v) else str(v)


def yearly(amount, interval):
    if blank(amount):
        return None
    factor = YEARLY_FACTOR.get(text(interval).lower(), 1)
    try:
        return round(float(amount) * factor)
    except (TypeError, ValueError):
        return None


def work_mode(job):
    title = text(job.get("title")).lower()
    loc = text(job.get("location")).lower()
    head = text(job.get("description"))[:1500].lower()
    if "hybrid" in title or "hybrid" in loc or re.search(r"\bhybrid\b", head):
        return "hybrid"
    if job.get("is_remote") is True or "remote" in title or "remote" in loc:
        return "remote"
    return "onsite"


def experience_level(title):
    t = text(title)
    if LEAD.search(t):
        return "lead"
    if SENIOR.search(t):
        return "senior"
    if ENTRY.search(t):
        return "entry"
    return "mid"


def job_types(job):
    raw = text(job.get("job_type")).lower().replace("-", "").replace(" ", "")
    return {p for p in raw.split(",") if p}


def job_key(job):
    def norm(s):
        return re.sub(r"[^a-z0-9]+", " ", text(s).lower()).strip()
    city = norm(text(job.get("location")).split(",")[0])
    return f"{norm(job.get('company'))}|{norm(job.get('title'))}|{city}"


def words_in(title, words):
    t = text(title).lower()
    return any(w.strip().lower() in t for w in words if w and w.strip())


def to_match(job):
    """Turn a raw JobSpy row (dict) into the shape the database expects."""
    smin = yearly(job.get("min_amount"), job.get("interval"))
    smax = yearly(job.get("max_amount"), job.get("interval"))
    posted = job.get("date_posted")
    types = sorted(job_types(job))
    return {
        "job_key": job_key(job),
        "title": text(job.get("title")),
        "company": text(job.get("company")),
        "location": text(job.get("location")),
        "work_mode": work_mode(job),
        "experience_level": experience_level(job.get("title")),
        "job_type": ",".join(types) or None,
        "salary_min": smin,
        "salary_max": smax,
        "site": text(job.get("site")),
        "job_url": text(job.get("job_url_direct")) or text(job.get("job_url")),
        "description": text(job.get("description"))[:4000],
        "date_posted": None if blank(posted) else str(posted)[:10],
    }


def keep(match, search):
    """Return (True, '') if the match passes the search's filters, else (False, reason)."""
    title = match["title"]
    if not title:
        return False, "no title"
    if search.get("must_include") and not words_in(title, search["must_include"]):
        return False, "missing must-include word"
    if search.get("exclude_words") and words_in(title, search["exclude_words"]):
        return False, "excluded word"
    modes = search.get("work_modes") or []
    if modes and match["work_mode"] not in modes:
        return False, "work mode"
    levels = search.get("experience_levels") or []
    if levels and match["experience_level"] not in levels:
        return False, "experience level"
    types = search.get("job_types") or []
    if types and match["job_type"] and not set(match["job_type"].split(",")) & set(types):
        return False, "job type"
    min_salary = search.get("min_salary")
    if min_salary:
        top = match["salary_max"] or match["salary_min"]
        if top is None:
            if not search.get("include_no_salary", True):
                return False, "no salary listed"
        elif top < min_salary:
            return False, "salary too low"
    return True, ""
