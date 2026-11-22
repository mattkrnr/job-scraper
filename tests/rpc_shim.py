"""Tiny stand-in for Supabase's REST API (PostgREST), RPC calls only, for local tests.

Behaves like PostgREST where the scraper cares:
  * POST /rest/v1/rpc/<fn> with a JSON object of named args, run as the `anon` role
  * setof / table functions -> JSON array, scalar -> JSON value, void -> 204 with an EMPTY body
  * SQL errors -> 4xx with a JSON {code, message}
CI runs the same tests against real PostgREST as well.
"""
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def psql(sql, variables=None, role=None):
    args = ["psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"]
    for k, v in (variables or {}).items():
        args += ["-v", f"{k}={v}"]
    pre = f"set role {role};\n" if role else ""
    p = subprocess.run(args + ["-f", "-"], input=pre + sql, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def signature(name):
    rc, out, err = psql(
        "select json_build_object('args', p.proargnames, 'types', p.proargtypes::regtype[]::text[], "
        "'set', p.proretset, 'ret', p.prorettype::regtype::text, 'nin', p.pronargs) "
        "from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = :'n'",
        {"n": name})
    if rc or not out:
        return None
    return json.loads(out.splitlines()[0])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body=None):
        self.send_response(code)
        data = b"" if body is None else json.dumps(body).encode()
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        prefix = "/rest/v1/rpc/"
        if not self.path.startswith(prefix):
            return self.send(404, {"message": "only rpc is supported"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send(415, {"message": "Content-Type must be application/json"})
        name = self.path[len(prefix):]
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        sig = signature(name)
        if not sig:
            return self.send(404, {"code": "PGRST202", "message": f"function {name} not found"})
        names = sig["args"][: sig["nin"]]
        if set(body) != set(names):
            return self.send(404, {"code": "PGRST202", "message": f"no {name} with args {sorted(body)}"})
        parts, variables = [], {}
        for i, (a, t) in enumerate(zip(names, sig["types"])):
            v = body[a]
            if v is None:
                parts.append(f"{a} => null::{t}")
                continue
            variables[f"a{i}"] = json.dumps(v) if t in ("jsonb", "json") else (str(v).lower() if isinstance(v, bool) else str(v))
            parts.append(f"{a} => :'a{i}'::{t}")
        call = f"public.{name}({', '.join(parts)})"
        if sig["set"]:
            sql = f"select coalesce(json_agg(r), '[]') from {call} r;"
        elif sig["ret"] == "void":
            sql = f"select {call};"
        else:
            sql = f"select to_json({call});"
        rc, out, err = psql(sql, variables, role="anon")
        if rc:
            msg = err.split("ERROR:", 1)[-1].strip().splitlines()[0] if "ERROR:" in err else err
            code = 403 if "bad token" in msg or "permission denied" in msg else 400
            return self.send(code, {"code": "P0001", "message": msg})
        if sig["ret"] == "void":
            return self.send(204)
        return self.send(200, json.loads(out) if out else None)


def serve(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv


if __name__ == "__main__":
    s = serve(int(os.environ.get("PORT", "54330")))
    print("listening", s.server_address)
    s.serve_forever()
