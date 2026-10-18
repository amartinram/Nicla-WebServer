"""
Is the production database reachable, and from where?

    python check_db.py

Prompts for the DATABASE_URL (hidden, like a password) so the connection
string never lands in your shell history or a terminal transcript. Reports
each stage separately, because "Neon is down" and "Render cannot reach Neon"
need different fixes and look identical from the outside.
"""
import getpass
import socket
import time
from urllib.parse import urlparse

url = getpass.getpass("DATABASE_URL (paste from Render > Environment): ").strip()
if not url:
    raise SystemExit("nothing pasted")

parsed = urlparse(url)
host, port = parsed.hostname, parsed.port or 5432
print(f"\nhost: {host}:{port}")

# 1. DNS. A Neon endpoint that was deleted stops resolving.
try:
    t = time.time()
    socket.getaddrinfo(host, port)
    print(f"[ok]   DNS resolves                      ({time.time()-t:.2f}s)")
except Exception as e:
    raise SystemExit(f"[FAIL] DNS does not resolve: {e}\n"
                     "       -> the Neon endpoint is gone or the URL is wrong.")

# 2. TCP. Neon's proxy accepts connections even while the compute is asleep,
#    so reaching this point says nothing about the database being usable.
try:
    t = time.time()
    socket.create_connection((host, port), timeout=15).close()
    print(f"[ok]   TCP connect                       ({time.time()-t:.2f}s)")
except Exception as e:
    raise SystemExit(f"[FAIL] TCP refused/timed out: {e}\n"
                     "       -> Neon is unreachable from this machine.")

# 3. Postgres handshake + a real query. This is the stage that hangs when the
#    compute cannot wake (exhausted quota, suspended project).
import psycopg
try:
    t = time.time()
    with psycopg.connect(url, connect_timeout=20) as conn:
        print(f"[ok]   Postgres authenticated            ({time.time()-t:.2f}s)")
        t = time.time()
        conn.execute("SET statement_timeout = '20s'")
        n = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'readings' ORDER BY ordinal_position").fetchall()]
    print(f"[ok]   Query ran                         ({time.time()-t:.2f}s)")
    print(f"\n       readings rows: {n}")
    print(f"       gait columns : {'speed_log' in cols and 'YES (migration applied)' or 'NO'}")
    print("\n=> The database is healthy from this machine. If Render still hangs,")
    print("   the DATABASE_URL on Render is stale or points at a different endpoint.")
except Exception as e:
    print(f"[FAIL] after {time.time()-t:.2f}s: {type(e).__name__}: {e}")
    print("\n=> Neon is not serving queries. Check the Neon console: is the project")
    print("   active, and are you within the free-plan compute hours?")
