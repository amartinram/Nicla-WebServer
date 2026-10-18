"""
Ask the running service whether it can reach its own database.

    python check_deploy.py

Prompts for the ingest token (hidden) so it never lands in shell history, calls
GET /healthz/db, and prints exactly what came back. A browser reports a slow
reply as "cannot connect", which hides the difference between the service
being down and the database connection hanging -- this does not.
"""
import getpass
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://stepcounter-g5lg.onrender.com"
TIMEOUT = 150

token = getpass.getpass("INGEST_TOKEN (hidden): ").strip()
if not token:
    raise SystemExit("nothing pasted")

# /healthz first: it never touches the database, so it separates "service down"
# from "database unreachable".
for path, note in ((f"{BASE}/healthz", "service itself"),
                   (f"{BASE}/healthz/db?token={urllib.parse.quote(token)}", "database")):
    print(f"\n--- {note}: {path.split('?')[0]}")
    started = time.time()
    try:
        with urllib.request.urlopen(path, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
            print(f"HTTP {r.status} in {time.time()-started:.2f}s")
            print(body.rstrip())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"HTTP {e.code} in {time.time()-started:.2f}s")
        print(body.rstrip())
        if e.code == 401:
            print(">> The token does not match INGEST_TOKEN on Render.")
            print(">> Your phone cannot upload with this token either.")
    except Exception as e:
        print(f"NO RESPONSE after {time.time()-started:.2f}s: {type(e).__name__}: {e}")
        print(">> The request hung rather than failing. If /healthz answered but")
        print(">> this did not, the database connection is not timing out at all.")
