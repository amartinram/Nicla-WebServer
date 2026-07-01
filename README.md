# StepCounter Cloud Server

Doctor-facing web dashboard and alerting layer for the Nicla Sense ME amputee
step-counter. Cloud stage of the pipeline:

```
Nicla Sense ME ──BLE──► patient phone (Android) ──Wi-Fi/HTTPS──► this server ──► doctor
```

Drop-in replacement for the original Google Apps Script test backend: the phone
POSTs the same form fields (`sheetName`, `steps`, `logData`, `captureTime`), so
only the app's `SERVER_URL` changes.

> **Scope.** This is a **proof of concept**. It implements security/privacy
> measures *aligned with* GDPR and medical-device software practice (IEC 62304,
> ISO 13485), but it is **not a certified medical device** — formal certification
> (MDR, Notified Body, CE marking) is an organisational process out of scope here.

## Security & privacy model

| Concern | Measure |
|---|---|
| Doctor-only access | Session **login** required on every data view (`DOCTOR_USER` + hashed password) |
| Authenticated ingest | Phone must include a secret **`INGEST_TOKEN`** in its `SERVER_URL` (`?token=…`) — no app code change |
| Pseudonymisation (GDPR) | Stores **only the device MAC + steps** — no names/PII. The MAC↔patient mapping stays with the clinician **offline**, never in the cloud. (A MAC is a *pseudonym*, still personal data.) |
| Transport security | **HTTPS enforced** (behind the host's TLS proxy) + secure cookies |
| Accountability | **Audit log** of logins, views and deletions |
| Right to erasure (GDPR) | **Delete-all-for-a-MAC** button on the patient page |
| Data residency | Deploy in an **EU region** (config below uses Frankfurt) with a provider DPA |

## Features

- **Ingest** (`POST /` or `/data`) — token-authenticated; returns HTTP 200 so the
  phone clears its retry cache.
- **Dashboard** (`/`) — one card per patient: status, latest daily steps, last seen.
- **Patient view** (`/patient/<mac>`) — daily-steps bar chart, per-minute cadence
  line chart, summary stats, GDPR delete.
- **Alerting** — background checker raises **SILENT** (no data > `ALERT_SILENT_HOURS`)
  and **LOW** (latest daily steps < `ALERT_MIN_STEPS`); shown on the dashboard and
  emailed if SMTP is configured.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DOCTOR_PASSWORD=changeme      # or DOCTOR_PASSWORD_HASH (see generate_hash.py)
export INGEST_TOKEN=devtoken
export FORCE_HTTPS=0                  # allow plain http on localhost

python seed_demo.py                  # optional: OK / LOW / SILENT demo patients
python app.py                        # http://localhost:8000
```

Log in at `/login` (user `doctor`). The demo seeds every dashboard state so you
can take screenshots for the article.

## Deploy free to Render (EU / HTTPS, no domain needed)

1. Push this folder to a GitHub repo.
2. Generate the password hash: `python generate_hash.py` → copy the
   `DOCTOR_PASSWORD_HASH=…` value.
3. In Render: **New → Blueprint**, select the repo (it reads `render.yaml`).
   `SECRET_KEY` and `INGEST_TOKEN` are auto-generated; paste `DOCTOR_PASSWORD_HASH`
   when prompted.
4. Render gives you `https://stepcounter.onrender.com` (HTTPS, Frankfurt).
5. In the Android app's **"URL servidor"** field, set:
   ```
   https://stepcounter.onrender.com/data?token=<INGEST_TOKEN>
   ```
   (copy `INGEST_TOKEN` from the Render dashboard → Environment).

> **Free-tier caveats:** the free web service **sleeps after ~15 min idle**
> (first request wakes it; the phone caches + retries, so no data is lost), and
> the free disk is **ephemeral** — SQLite resets on redeploy. Fine for the
> article/demo. For the clinical deployment see below.

## Path to clinical deployment

1. **Durable storage** — move from SQLite to managed **Postgres** (or a paid
   persistent disk). All DB access is isolated in `app.py`, so this is localized.
2. **Always-on plan** — remove the free-tier sleep.
3. **Hardening** — CSRF protection, rate limiting, per-clinician accounts/roles,
   key rotation, log retention.
4. **Compliance** — provider **DPA**, EU/health-certified hosting (e.g. HDS),
   documented under a QMS (ISO 13485 / IEC 62304) for any CE-marking effort.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `DOCTOR_USER` | `doctor` | Login user |
| `DOCTOR_PASSWORD_HASH` | — | werkzeug hash (preferred); else `DOCTOR_PASSWORD` |
| `INGEST_TOKEN` | *(empty=open)* | Secret the phone must send as `?token=` |
| `SECRET_KEY` | random | Session signing key (set a fixed one in prod) |
| `FORCE_HTTPS` | `1` | Redirect to HTTPS + secure cookies (set `0` for localhost) |
| `DB_PATH` | `stepcounter.db` | SQLite path |
| `ALERT_SILENT_HOURS` | `26` | Silence before a SILENT alert |
| `ALERT_MIN_STEPS` | `1000` | Daily-step floor (placeholder — set clinically) |
| `ALERT_CHECK_MIN` | `30` | Background check cadence (min) |
| `ALERT_COOLDOWN_H` | `12` | Min hours between repeat notifications |
| `SMTP_HOST/PORT/USER/PASS`, `ALERT_FROM/TO` | — | Optional email alerts |
