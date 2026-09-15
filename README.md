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

Local runs use a plain SQLite file — no database to install. (Leave
`DATABASE_URL` unset; setting it switches the app to Postgres.)

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

## Deploy free to Render + Neon (EU / HTTPS, no domain needed)

> **Why two services.** Render's free filesystem is **ephemeral**: it is wiped
> every time the service redeploys *or sleeps* (15 min idle). Since the device
> uploads roughly once a day, the server would be asleep — and therefore reset —
> between nearly every upload, so a local SQLite file would lose almost all data.
> The database therefore lives on a separate managed Postgres. Both services are
> free and both sit in the **EU**, so patient data never leaves it.

**1. Database — Neon (free, EU).** Create a project at neon.tech, **region
`AWS eu-central-1` (Frankfurt)**. Copy the connection string:

```
postgresql://<user>:<password>@<host>.eu-central-1.aws.neon.tech/<db>?sslmode=require
```

**2. Password hash.** `python generate_hash.py` → copy the `DOCTOR_PASSWORD_HASH=…` value.

**3. Web service — Render (free, Frankfurt).** **New → Blueprint**, select this
repo (it reads `render.yaml`). `SECRET_KEY` and `INGEST_TOKEN` are auto-generated;
paste `DOCTOR_PASSWORD_HASH` and the Neon `DATABASE_URL` when prompted.

**4.** Render gives you `https://stepcounter.onrender.com` (HTTPS, Frankfurt) —
a stable URL, unlike the rotating `start.sh` tunnel.

**5.** In the Android app's **"URL servidor"** field, set:
```
https://stepcounter.onrender.com/data?token=<INGEST_TOKEN>
```
(copy `INGEST_TOKEN` from Render → Environment).

### Keeping the service awake (`.github/workflows/keep-awake.yml`)

The free plan stops a web service after 15 minutes without traffic. Data is
never at risk — it lives in the database, not on the container — and a slow
first request only delays an upload, which the phone retries.

The real problem is **alerting**. The alert thread runs only while the service
is awake, and a `SILENT` alert means *no data has arrived from this patient*.
No data means nothing wakes the service, so the alert that matters most would
never fire: a patient could stop using the device entirely and the only way to
find out would be to open the dashboard manually.

A scheduled GitHub Action therefore pings `/healthz` every 10 minutes to hold
the service up. Two limits to know about:

- GitHub runs scheduled workflows **best-effort** and delays them under load,
  so the service will occasionally nap. The ping wakes it again.
- GitHub **disables scheduled workflows after 60 days of repository
  inactivity**. On a long deployment, re-enable it in the Actions tab (or
  push any commit). A more reliable alternative is an external monitor such as
  cron-job.org or UptimeRobot pointed at the same URL.

Staying awake continuously uses roughly **730 of the free tier's 750 instance
hours per month**, so this only fits while this is the *only* free web service
on the account. Adding another would exhaust the quota and stop both.

## Path to clinical deployment

1. ~~**Durable storage**~~ — **done.** `db.py` runs the app on SQLite locally and
   managed **Postgres** in production from one set of SQL statements; set
   `DATABASE_URL` to switch.
2. **Always-on plan** — remove the free-tier sleep, so the alert thread runs on
   schedule rather than only while the service is awake.
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
| `PROXY_HOPS` | `3` | Proxies in front of the app. Decides which `X-Forwarded-For` entry is the client, so the audit log names the phone and not the proxy. `3` is measured for Render (Cloudflare + Render's router); use `1` behind a single nginx |
| `DATABASE_URL` | *(unset)* | Postgres connection string. **Set = production**, unset = local SQLite |
| `DB_PATH` | `stepcounter.db` | SQLite file, used only when `DATABASE_URL` is unset |
| `ALERT_SILENT_HOURS` | `26` | Silence before a SILENT alert |
| `ALERT_MIN_STEPS` | `1000` | Daily-step floor (placeholder — set clinically) |
| `ALERT_CHECK_MIN` | `30` | Background check cadence (min) |
| `ALERT_COOLDOWN_H` | `12` | Min hours between repeat notifications |
| `SMTP_HOST/PORT/USER/PASS`, `ALERT_FROM/TO` | — | Optional email alerts |
