#!/usr/bin/env bash
# Bring the StepCounter server up locally + expose it over an HTTPS tunnel.
# First run creates .env with stable secrets; later runs reuse them, so the
# ingest token and doctor password survive reboots.
set -euo pipefail
cd "$(dirname "$0")"

# --- 0. Activate the virtualenv (Flask/gunicorn live here) -------------------
source .venv/bin/activate

# --- 1. Load or create stable secrets ---------------------------------------
if [ ! -f .env ]; then
  echo "[start] first run — generating .env with fresh secrets"
  # Plain password (app hashes it at startup). Values are token-safe/quoted so
  # bash can source them — do NOT store a werkzeug hash here ($ and : break it).
  {
    echo "SECRET_KEY='$(python -c 'import secrets;print(secrets.token_hex(32))')'"
    echo "INGEST_TOKEN='$(python -c 'import secrets;print(secrets.token_urlsafe(24))')'"
    echo "DOCTOR_PASSWORD='step-$(python -c 'import secrets;print(secrets.token_hex(4))')'"
    echo "FORCE_HTTPS='1'"
  } > .env
fi
set -a; source .env; set +a

# --- 2. Start the app --------------------------------------------------------
# Match patterns unique to the running servers (not this script or a wrapper).
pkill -f 'app:app --workers 1 --threads'   2>/dev/null || true
pkill -f 'cloudflared tunnel --url http'    2>/dev/null || true
sleep 1

gunicorn app:app --workers 1 --threads 4 --bind 127.0.0.1:8000 \
  --access-logfile - --error-logfile - > gunicorn.log 2>&1 &
echo "[start] gunicorn up on 127.0.0.1:8000 (log: gunicorn.log)"

# --- 3. Public HTTPS tunnel --------------------------------------------------
# Cloudflare's quick-tunnel request sometimes times out ("context deadline
# exceeded"); retry a few times. The real URL is a *.trycloudflare.com host
# other than api.trycloudflare.com (which only appears in error lines).
tunnel_url() {
  grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' tunnel.log \
    | grep -v 'api\.trycloudflare\.com' | head -1
}

URL=""
for attempt in 1 2 3; do
  ./cloudflared tunnel --url http://127.0.0.1:8000 > tunnel.log 2>&1 &
  echo "[start] tunnel attempt $attempt — waiting for URL..."
  for i in $(seq 1 20); do
    URL=$(tunnel_url || true)
    [ -n "$URL" ] && break
    sleep 1
  done
  [ -n "$URL" ] && break
  echo "[start] tunnel attempt $attempt failed, retrying..."
  pkill -f 'cloudflared tunnel --url http' 2>/dev/null || true
  sleep 2
done

echo
echo "======================================================================"
echo " Dashboard : ${URL:-<not ready, check tunnel.log>}"
echo " App URL   : ${URL}/data?token=${INGEST_TOKEN}"
echo " Login     : user 'doctor'  password: ${DOCTOR_PASSWORD}"
echo "======================================================================"
