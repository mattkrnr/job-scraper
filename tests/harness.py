"""Shared helpers: build a fresh test database, run SQL, run the real scraper as a subprocess."""
import glob
import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER = "2f388ba8-e793-41fd-a664-a582aeb33655"
FRIEND = "11111111-1111-1111-1111-111111111111"
STRANGER = "22222222-2222-2222-2222-222222222222"
EMAILS = {OWNER: "owner@example.com", FRIEND: "friend@example.com", STRANGER: "stranger@example.com"}
TOKEN = "test-token"


def psql(sql, role=None, user=None, check=True):
    """Run SQL; returns stdout lines. role/user simulate a PostgREST request (JWT claims)."""
    pre = ""
    if user:
        claims = json.dumps({"sub": user, "email": EMAILS[user], "role": "authenticated"})
        pre = f"set request.jwt.claims = '{claims}';\n"
    if role:
        pre += f"set role {role};\n"
    p = subprocess.run(["psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-f", "-"],
                       input=pre + sql, capture_output=True, text=True)
    if check and p.returncode:
        raise AssertionError(f"SQL failed: {p.stderr.strip()}\n--- SQL ---\n{sql}")
    return p if not check else [l for l in p.stdout.splitlines() if l.strip()]


def q1(sql, **kw):
    out = "\n".join(psql(sql, **kw)).strip()
    if not out:
        return None
    return json.loads(out) if out.startswith(("{", "[")) else out


def build_db(name=None):
    """Create a fresh database from the real migrations and point PGDATABASE at it."""
    name = name or "jt_" + uuid.uuid4().hex[:10]
    os.environ["PGDATABASE"] = "postgres"
    psql(f"create database {name};")
    os.environ["PGDATABASE"] = name
    files = [os.path.join(ROOT, "tests/db/00_supabase_stub.sql")]
    files += sorted(glob.glob(os.path.join(ROOT, "supabase/migrations/*.sql")))
    files += [os.path.join(ROOT, "tests/db/10_seed.sql")]
    for f in files:
        with open(f) as fh:
            psql(fh.read())
    psql("notify pgrst, 'reload schema';")  # harmless without PostgREST; refreshes its cache in CI
    return name


def drop_db(name):
    os.environ["PGDATABASE"] = "postgres"
    psql(f"drop database if exists {name} with (force);", check=False)


def start_rest():
    """Returns (base_url, stop_fn) for the local PostgREST stand-in (see rpc_shim.py; test_rest_parity.py checks it)."""
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import rpc_shim
    srv = rpc_shim.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", lambda: (srv.shutdown(), srv.server_close())


def run_scraper(base_url, *args, token=TOKEN, scenario="normal"):
    log = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl").name
    env = {**os.environ, "SUPABASE_URL": base_url, "FAKE_JOBSPY_SCENARIO": scenario, "FAKE_JOBSPY_LOG": log,
           "PYTHONPATH": os.path.join(ROOT, "tests/fakes"), "RESULTS_PER_QUERY": "10", "QUERY_PAUSE": "0"}
    if token is None:
        env.pop("JOB_SCRAPER_TOKEN", None)
    else:
        env["JOB_SCRAPER_TOKEN"] = token
    env.pop("DRY_RUN", None)
    p = subprocess.run([sys.executable, "scrape.py", *args], cwd=os.path.join(ROOT, "scraper"),
                       env=env, capture_output=True, text=True, timeout=120)
    with open(log) as fh:
        calls = [json.loads(l) for l in fh if l.strip()]
    os.unlink(log)
    return p, calls
