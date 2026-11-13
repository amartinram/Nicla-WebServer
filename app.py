"""
StepCounter cloud server — doctor-facing dashboard + alerting.

Pipeline:  Nicla --BLE--> patient phone --Wi-Fi/HTTPS--> THIS SERVER --> doctor

Security model (proof-of-concept, designed toward GDPR / IEC 62304 principles):
  * Doctor-only access  -> session login on every data view (DOCTOR_USER / hash).
  * Authenticated ingest -> phone must include a secret token in the SERVER_URL,
    so only the paired app can POST (no app code change: token rides in the URL).
  * Pseudonymisation     -> only the device MAC + steps are stored; NO names/PII.
    The MAC<->patient mapping stays with the clinician offline, never in the cloud.
  * Transport security    -> HTTPS enforced (behind the host's TLS proxy).
  * Accountability        -> audit log of logins, views and deletions.
  * Right to erasure      -> delete-all-data-for-a-MAC endpoint.

NOTE: this is NOT a certified medical device. Formal certification (MDR, ISO
13485, IEC 62304, Notified Body / CE marking) is an organisational process that
is out of scope here; the code only implements measures that *support* it.

Drop-in for the original Apps Script: the phone still POSTs form fields
sheetName / steps / logData / captureTime.
"""

import os
import re
import time
import smtplib
import collections
import statistics
import secrets
import threading
from functools import wraps
from email.message import EmailMessage

from flask import (Flask, request, render_template, redirect, url_for,
                   session, g, abort, flash)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

import i18n
from db import (connect, init_schema, IS_POSTGRES, STATEMENT_TIMEOUT_MS,
                CONNECT_TIMEOUT_S, FORCE_IPV4)

# ----------------------------------------------------------------------------
# Configuration (override with environment variables)
# ----------------------------------------------------------------------------
# Storage is selected in db.py: DATABASE_URL (Postgres, production) else
# DB_PATH (SQLite, local development).
ALERT_SILENT_HOURS = float(os.environ.get("ALERT_SILENT_HOURS", "26"))
ALERT_MIN_STEPS    = int(os.environ.get("ALERT_MIN_STEPS", "1000"))   # per-patient default/fallback
ALERT_MIN_CADENCE  = float(os.environ.get("ALERT_MIN_CADENCE", "0"))  # per-patient default (0 = off)
ALERT_MIN_MINUTES  = int(os.environ.get("ALERT_MIN_MINUTES", "0"))    # per-patient default (0 = off)
ALERT_MIN_SPEED    = float(os.environ.get("ALERT_MIN_SPEED", "0"))    # m/s, per-patient default (0 = off)
ALERT_BATTERY_MIN  = int(os.environ.get("ALERT_BATTERY_MIN", "20"))   # global battery % floor
ALERT_CHECK_MIN    = float(os.environ.get("ALERT_CHECK_MIN", "30"))
ALERT_COOLDOWN_H   = float(os.environ.get("ALERT_COOLDOWN_H", "12"))
# The audit log gains a row for every page view; rows older than this are
# pruned by the alert thread so it cannot fill a small managed database.
AUDIT_RETENTION_DAYS = int(os.environ.get("AUDIT_RETENTION_DAYS", "365"))   # 0 = keep forever
# Days shown on a patient page unless ?days=N or ?days=all asks otherwise. Each
# day is a 1440-value log (two with gait), so an unbounded page grows by
# several kB per day of monitoring.
PATIENT_DAYS = int(os.environ.get("PATIENT_DAYS", "90"))

# Battery arrives as a separate stream "<MAC>_Battery" whose "steps" value is the
# battery percentage. We fold it back into its parent patient (see evaluate()).
BATTERY_SUFFIX = "_Battery"

# The device does not report charge. Its "battery percentage" is the charger's
# voltage monitor: the cell voltage as a percentage of the 4.2 V charge target,
# in 2 % steps. A cell at 3.70 V therefore reads 88 while holding about a
# seventh of its charge, and the device shuts down at a reading near 70 -- so a
# threshold in those units could never mean what its "%" suggested, and the
# default of 20 could not fire at all. The reading is converted back to volts
# and then to an estimated state of charge on a typical single-cell LiPo
# resting curve. It is an estimate: the gauge's steps are 84 mV wide.
BATTERY_FULL_V = 4.2
BATTERY_GAUGE_STEP = 2          # the reading means "between this and this + 2"
LIPO_VOLTS_TO_SOC = (
    (4.20, 100), (4.15, 95), (4.11, 90), (4.08, 85), (4.02, 80), (3.98, 75),
    (3.95, 70), (3.91, 65), (3.87, 60), (3.85, 55), (3.84, 50), (3.82, 45),
    (3.80, 40), (3.79, 35), (3.77, 30), (3.75, 25), (3.73, 20), (3.71, 15),
    (3.69, 10), (3.61, 5), (3.27, 0),
)


def battery_state(gauge):
    """Volts and estimated charge for a reported gauge value, or None when the
    device could not read its battery (it reports that as 254 or 255)."""
    if gauge is None or not 0 < gauge <= 100:
        return None
    volts = min(BATTERY_FULL_V,
                BATTERY_FULL_V * (gauge + BATTERY_GAUGE_STEP / 2) / 100)
    soc = 0.0
    for (v_hi, soc_hi), (v_lo, soc_lo) in zip(LIPO_VOLTS_TO_SOC, LIPO_VOLTS_TO_SOC[1:]):
        if volts >= v_lo:
            soc = soc_lo + (soc_hi - soc_lo) * (min(volts, v_hi) - v_lo) / (v_hi - v_lo)
            break
    return {"gauge": gauge, "volts": round(volts, 2), "soc": round(soc)}


# Walking speed travels as one byte per minute to keep the device's BLE dump
# small; this is the metre-per-second value of one unit. It must stay in step
# with Config::GAIT_SPEED_LSB in the firmware. A stored 0 is not "standing
# still" -- it is "no gait was measured in that minute".
GAIT_SPEED_LSB = 0.02
# Stride-time variability arrives in 0.1 % units, shank excursion in 0.1 deg.
GAIT_CV_LSB  = 0.1
GAIT_EXC_LSB = 0.1

# A walking bout is a run of consecutive minutes with steps recorded. Bouts are
# sorted by how long they last, because the length says what kind of walking it
# was: a single minute is a few steps around the room, a handful of minutes is
# moving about the house, five or more is sustained walking. The log has one
# value per minute, so a bout's length is a whole number of minutes.
BOUT_SHORT_MAX = 1      # minutes: at most this long -> "short"
BOUT_LONG_MIN  = 5      # minutes: at least this long -> "long"; between -> "medium"
BOUT_GROUPS = ("short", "medium", "long")


def bout_labels(lang):
    """The three groups' names in `lang`, in BOUT_GROUPS order."""
    return [i18n.t("{n} min or less", lang, n=BOUT_SHORT_MAX),
            i18n.t("{a} to {b} min", lang, a=BOUT_SHORT_MAX + 1, b=BOUT_LONG_MIN - 1),
            i18n.t("{n} min or more", lang, n=BOUT_LONG_MIN)]
# The first and last minute of a bout are only partly walked, so a bout needs at
# least this many minutes before it holds one that was walked from start to end.
STEADY_BOUT_MIN = 3

# Auth / security
SECRET_KEY    = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
DOCTOR_USER   = os.environ.get("DOCTOR_USER", "doctor")
# Prefer DOCTOR_PASSWORD_HASH (a werkzeug hash). Fall back to a plain password
# (hashed at startup) for convenience, or a dev default with a loud warning.
_pw_hash      = os.environ.get("DOCTOR_PASSWORD_HASH")
_pw_plain     = os.environ.get("DOCTOR_PASSWORD")
INGEST_TOKEN  = os.environ.get("INGEST_TOKEN", "")          # required to POST data
FORCE_HTTPS   = os.environ.get("FORCE_HTTPS", "1") == "1"

# Email (optional)
SMTP_HOST  = os.environ.get("SMTP_HOST", "")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER  = os.environ.get("SMTP_USER", "")
SMTP_PASS  = os.environ.get("SMTP_PASS", "")
ALERT_FROM = os.environ.get("ALERT_FROM", SMTP_USER)
ALERT_TO   = os.environ.get("ALERT_TO", "")
# Language of the alert emails; the doctors read Spanish. Changeable in Settings.
ALERT_LANG = i18n.norm_lang(os.environ.get("ALERT_LANG", "es"))

app = Flask(__name__)
app.secret_key = SECRET_KEY
# Trust the host's reverse proxy for scheme/host (Render, Fly, nginx...).
#
# PROXY_HOPS is how many proxies sit in front of the app, and it decides which
# entry of X-Forwarded-For is treated as the client. ProxyFix counts from the
# RIGHT, so the value must match the real chain: too low and the audit log
# records the proxy instead of the patient's phone, too high and a caller can
# forge their own address by sending an X-Forwarded-For header.
#
# Measured against the deployed service, which sits behind Cloudflare:
#   <client>, <cloudflare edge>, <render internal router>
# so the client is the 3rd entry from the right. 1 gave a private 10.x address
# and 2 gave a Cloudflare address (162.158.x / 172.70.x) -- both varying per
# request, which is the tell that they are infrastructure and not the caller.
# If len(X-Forwarded-For) < PROXY_HOPS, ProxyFix falls back to the socket peer
# rather than guessing, so a shorter chain degrades safely.
PROXY_HOPS = int(os.environ.get("PROXY_HOPS", "3"))
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXY_HOPS, x_proto=1, x_host=1)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=FORCE_HTTPS,
    # A day's upload is under 15 kB. Without a ceiling one request could hand
    # the worker an arbitrarily large body to parse.
    MAX_CONTENT_LENGTH=1024 * 1024,
)

if _pw_hash:
    DOCTOR_PW_HASH = _pw_hash
elif _pw_plain:
    DOCTOR_PW_HASH = generate_password_hash(_pw_plain)
else:
    DOCTOR_PW_HASH = generate_password_hash("changeme")
    app.logger.warning("[security] No DOCTOR_PASSWORD set — using dev default "
                       "'changeme'. Set DOCTOR_PASSWORD_HASH in production.")

if not INGEST_TOKEN:
    app.logger.warning("[security] No INGEST_TOKEN set — ingest endpoint is OPEN. "
                       "Set INGEST_TOKEN and append ?token=... to the app's SERVER_URL.")


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
_schema_ready = False
_schema_last_try = 0.0
_schema_lock = threading.Lock()
SCHEMA_RETRY_SECONDS = 60


def ensure_schema():
    """Apply the schema once per process, on whichever request gets there first.

    Never blocks and never retries in a tight loop. Both matter when the
    database is the thing that is broken: a blocking lock makes every request
    queue behind another request's doomed attempt, so one slow failure becomes
    a worker with all its threads parked, which stops /healthz being *served*
    even though it needs no database -- and that is what the host reads as a
    dead instance. Failing fast and letting the request surface its own error
    keeps the service diagnosable instead of merely absent.
    """
    global _schema_ready, _schema_last_try
    if _schema_ready:
        return
    if not _schema_lock.acquire(blocking=False):
        return                      # another thread is already trying; don't queue
    try:
        if _schema_ready or (time.time() - _schema_last_try) < SCHEMA_RETRY_SECONDS:
            return
        _schema_last_try = time.time()
        init_schema(SETTINGS_DEFAULTS)
        _schema_ready = True
    finally:
        _schema_lock.release()


def get_db():
    if "db" not in g:
        ensure_schema()
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# The schema, migrations and default-seeding live in db.py so they can be
# expressed once and applied to either engine (see init_schema()).


# Configurable through the dashboard's Settings page; env values are the defaults.
SETTINGS_DEFAULTS = {
    "smtp_host":    SMTP_HOST,
    "smtp_port":    str(SMTP_PORT),
    "smtp_user":    SMTP_USER,
    "smtp_pass":    SMTP_PASS,
    "alert_from":   ALERT_FROM,
    "alert_to":     ALERT_TO,
    "silent_hours": str(ALERT_SILENT_HOURS),
    "battery_min":  str(ALERT_BATTERY_MIN),
    "alert_lang":   ALERT_LANG,
}


def get_settings(db):
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    s = dict(SETTINGS_DEFAULTS)
    s.update({r[0]: r[1] for r in rows})   # index-safe (works with or without Row factory)
    return s


# Per-patient activity thresholds. A value of 0 disables that metric's alert
# (steps default to ALERT_MIN_STEPS so existing behaviour is preserved).
PATIENT_DEFAULTS = {"min_steps": ALERT_MIN_STEPS,
                    "min_cadence": ALERT_MIN_CADENCE,
                    "min_minutes": ALERT_MIN_MINUTES,
                    "min_speed": ALERT_MIN_SPEED}


def _row_to_config(row):
    return {"min_steps": int(row[0]), "min_cadence": float(row[1]),
            "min_minutes": int(row[2]), "min_speed": float(row[3])}


def get_patient_config(db, device_id):
    """This patient's thresholds, falling back to PATIENT_DEFAULTS when unset."""
    row = db.execute("SELECT min_steps, min_cadence, min_minutes, min_speed"
                     " FROM patient_config WHERE device_id = ?", (device_id,)).fetchone()
    return _row_to_config(row) if row else dict(PATIENT_DEFAULTS)


def get_all_patient_configs(db):
    return {r[0]: _row_to_config(r[1:]) for r in
            db.execute("SELECT device_id, min_steps, min_cadence, min_minutes, min_speed"
                       " FROM patient_config").fetchall()}


def get_aliases(db):
    """Map of device_id -> doctor-chosen label (e.g. 'User1'). Missing devices
    simply show their MAC. Labels are pseudonyms too; keep them non-identifying."""
    return {r[0]: r[1] for r in
            db.execute("SELECT device_id, label FROM device_alias").fetchall()}


def known_devices(db):
    """Distinct patient MACs that have sent data (battery streams folded away)."""
    rows = db.execute("SELECT DISTINCT device_id FROM readings ORDER BY device_id").fetchall()
    return [r[0] for r in rows if not r[0].endswith(BATTERY_SUFFIX)]


def parse_log(s):
    """Comma-separated per-minute log -> list of ints. Tolerates a trailing
    comma and any stray field, because a truncated upload must still plot."""
    return [int(x) for x in (s or "").split(",") if x.strip().lstrip("-").isdigit()]


def walking_bouts(minutes):
    """Runs of consecutive minutes with steps, as (start_minute, length) pairs."""
    bouts, start = [], None
    for i, v in enumerate(minutes):
        if v > 0 and start is None:
            start = i
        elif v <= 0 and start is not None:
            bouts.append((start, i - start))
            start = None
    if start is not None:
        bouts.append((start, len(minutes) - start))
    return bouts


def bout_group(length):
    """Index into BOUT_GROUPS for a bout lasting `length` minutes."""
    if length <= BOUT_SHORT_MAX:
        return 0
    return 2 if length >= BOUT_LONG_MIN else 1


def mean_or_none(xs):
    """Mean rounded to a whole number, or None for no samples."""
    return round(statistics.fmean(xs)) if xs else None


def sd_or_none(xs):
    """Sample standard deviation rounded to a whole number, or None when there
    are fewer than two samples and the spread is undefined."""
    return round(statistics.stdev(xs)) if len(xs) > 1 else None


def day_activity(minutes, gait=None):
    """Everything the step log says about one day: active minutes, the walking
    bouts and their three duration groups, and the day's cadence.

    Cadence is defined once, here, and every view and alert reads it from this
    result. It means steps per minute *while walking*. The plain average over
    active minutes is not that: a minute in which the patient walked for twenty
    seconds counts as a third of their real cadence, so the average sinks with
    every short walk. The day's cadence is therefore, in order of preference:

      * "gait"  -- taken from the measured stride time, when the day has gait data;
      * "steps" -- the mean of the fully walked minutes, i.e. the minutes inside
                   a bout of STEADY_BOUT_MIN or more, leaving out its partly
                   walked first and last minute;
      * None    -- neither exists. The day had only short walks and no gait
                   burst, and a cadence cannot be read from partial minutes.
    """
    bouts = walking_bouts(minutes)
    # Per group, the samples behind its mean ± SD figures: the step count of
    # every minute walked (cadence) and the step total of every bout (steps).
    per_minute = [[] for _ in BOUT_GROUPS]
    per_bout = [[] for _ in BOUT_GROUPS]
    steady = []
    for start, length in bouts:
        k = bout_group(length)
        per_minute[k].extend(minutes[start:start + length])
        per_bout[k].append(sum(minutes[start:start + length]))
        if length >= STEADY_BOUT_MIN:
            steady.extend(minutes[start + 1:start + length - 1])

    def summary(key, mins, steps):
        return {"key": key, "bouts": len(steps),
                "minutes": len(mins), "steps": sum(steps),
                "cadence_mean": mean_or_none(mins), "cadence_sd": sd_or_none(mins),
                "steps_mean": mean_or_none(steps), "steps_sd": sd_or_none(steps)}

    groups = [summary(key, per_minute[k], per_bout[k])
              for k, key in enumerate(BOUT_GROUPS)]
    overall = summary("all",
                      [v for m in per_minute for v in m], [v for b in per_bout for v in b])

    if gait and gait["cadence"]:
        cadence, source = gait["cadence"], "gait"
    elif steady:
        cadence, source = round(sum(steady) / len(steady)), "steps"
    else:
        cadence, source = None, None

    return {
        "active_minutes": sum(length for _, length in bouts),
        "bouts": [[start, length, bout_group(length)] for start, length in bouts],
        "groups": groups,
        "overall": overall,
        "cadence": cadence,
        "cadence_source": source,
    }


def log_start_minute(captured_ms, n_minutes):
    """Minute of the day, 0-1439 in the display zone, at which a per-minute log
    begins.

    The device has no clock and starts counting when it is switched on, so its
    "day" is 24 hours that begin at an arbitrary time, not at midnight. A
    reading is stamped with the moment that day ended, which puts its first
    minute `n_minutes` earlier.
    """
    start = i18n.local_dt(captured_ms - n_minutes * 60_000)
    return start.hour * 60 + start.minute


def speed_series(speed_log):
    """Per-minute walking speed in m/s, with None for the minutes the device
    did not measure. None rather than 0 matters: the device only samples gait
    in short bursts, so a zero would draw a flat line through every minute it
    simply did not look at, and read as the patient having stopped."""
    return [round(v * GAIT_SPEED_LSB, 2) if v > 0 else None for v in parse_log(speed_log)]


def gait_metrics(row):
    """Day-level gait figures for one reading, or None when it carries none.

    Everything here except speed and stride length is measured directly from
    the gait events; those two rest on the firmware's stride-length model and
    are only as good as its per-patient calibration.
    """
    try:
        cycles = row["gait_cycles"]
    except (IndexError, KeyError):
        return None                       # pre-migration row
    if not cycles:
        return None

    stride_ms = row["stride_ms"] or 0
    stance = row["stance_pct"] or 0
    return {
        "cycles": cycles,
        "speed_ms": round((row["mean_speed"] or 0) * GAIT_SPEED_LSB, 2),
        "stance_pct": stance,
        "swing_pct": (100 - stance) if stance else 0,
        "stride_ms": stride_ms,
        "stride_cm": row["stride_cm"] or 0,
        "stride_cv_pct": round((row["stride_cv"] or 0) * GAIT_CV_LSB, 1),
        "excursion_deg": round((row["excursion_01"] or 0) * GAIT_EXC_LSB, 1),
        # One stride is two steps, so cadence follows from the stride time.
        "cadence": round(120000 / stride_ms) if stride_ms else 0,
    }


def save_settings(db, values):
    for k, v in values.items():
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v))
    db.commit()


def audit(action, target=None, actor=None):
    actor = actor or session.get("user") or request.remote_addr or "anonymous"
    try:
        db = get_db()
        db.execute("INSERT INTO audit (ts, actor, action, target) VALUES (?,?,?,?)",
                   (int(time.time() * 1000), actor, action, target))
        db.commit()
    except Exception as e:  # noqa: BLE001 - auditing must not break the request
        app.logger.error("[audit] failed: %s", e)


# ----------------------------------------------------------------------------
# Security helpers
# ----------------------------------------------------------------------------
HEALTH_PATH = "/healthz"


@app.before_request
def enforce_https():
    # The host's health check probes the container directly over plain HTTP, so
    # it never carries X-Forwarded-Proto. Redirecting it would return 301 and be
    # read as unhealthy, so this one path is exempt. It exposes nothing.
    if request.path == HEALTH_PATH:
        return
    if FORCE_HTTPS and not request.is_secure and request.method == "GET":
        # request.is_secure already honours X-Forwarded-Proto via ProxyFix.
        url = request.url.replace("http://", "https://", 1)
        return redirect(url, code=301)


def token_ok(supplied):
    """Constant-time check of an ingest token. Compared as bytes because
    compare_digest raises on a non-ASCII str, which turned a junk token into a
    500 instead of a 401."""
    return secrets.compare_digest((supplied or "").encode(), INGEST_TOKEN.encode())


# Every page is self-contained except for Chart.js, which is pinned to an exact
# version with a hash in patient.html. 'unsafe-inline' is needed by the pages'
# own inline scripts and styles; the policy still stops scripts from any other
# origin, framing, and forms posting anywhere else.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if FORCE_HTTPS:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if session.get("user"):
        # Patient pages must not be kept by the browser or a shared proxy.
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# CSRF. The session cookie is SameSite=Lax, which already keeps it off
# cross-site POSTs in current browsers; the token is the second line, and the
# one that does not depend on the browser. The phone's upload and the login
# form carry no session to ride on, so they are exempt.
CSRF_EXEMPT = {"ingest", "login"}


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@app.before_request
def csrf_protect():
    if request.method != "POST" or request.endpoint in CSRF_EXEMPT:
        return
    expected = session.get("csrf", "")
    supplied = request.form.get("csrf_token", "")
    if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
        abort(400)


# Login throttle: there is one account, so guessing its password is the whole
# attack. Held in memory, which is enough for the single worker this runs on.
LOGIN_MAX_FAILURES = 8
LOGIN_WINDOW_S = 600
_login_failures = collections.defaultdict(collections.deque)
_login_lock = threading.Lock()


def _recent_failures(ip, now):
    attempts = _login_failures[ip]
    while attempts and now - attempts[0] > LOGIN_WINDOW_S:
        attempts.popleft()
    return attempts


def login_blocked(ip):
    with _login_lock:
        blocked = len(_recent_failures(ip, time.time())) >= LOGIN_MAX_FAILURES
        if not _login_failures[ip]:
            del _login_failures[ip]
        return blocked


def login_failed(ip):
    with _login_lock:
        if len(_login_failures) > 10_000:       # never let the table itself grow unbounded
            _login_failures.clear()
        now = time.time()
        _recent_failures(ip, now).append(now)


def safe_next(target):
    """`target` if it is a path on this site, else the dashboard. The value
    comes from the query string, so anything else would let a crafted login
    link send a doctor who has just typed their password to another site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("dashboard")


@app.route(HEALTH_PATH)
def healthz():
    """Liveness only — deliberately does not touch the database. The app and the
    database sleep independently; failing this check on a cold database would
    make the host restart a perfectly healthy web service."""
    return "ok\n", 200


@app.route(HEALTH_PATH + "/db")
def healthz_db():
    """Why the database is unreachable, without needing the host's log viewer.

    Deliberately separate from /healthz, which must never touch the database:
    the host's health check has a few seconds to answer and the whole point of
    that endpoint is to keep reporting while the database is the broken part.

    Behind the ingest token, because the error text names the host it failed to
    reach. It never discloses the connection string or the credentials.
    """
    if INGEST_TOKEN and not token_ok(request.values.get("token", "")):
        abort(401)

    lines = [f"engine: {'postgres' if IS_POSTGRES else 'sqlite'}",
             f"connect_timeout_s: {CONNECT_TIMEOUT_S} (per resolved address)",
             f"force_ipv4: {FORCE_IPV4}",
             f"statement_timeout_ms: {STATEMENT_TIMEOUT_MS}",
             f"schema_applied: {_schema_ready}"]
    started = time.time()
    try:
        db = connect()
        lines.append(f"connect: ok in {time.time() - started:.2f}s")
        t = time.time()
        db.execute("SELECT 1").fetchone()
        lines.append(f"query: ok in {time.time() - t:.2f}s")
        try:
            cols = [r[0] for r in db.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'readings'").fetchall()] if IS_POSTGRES else [
                r[1] for r in db.execute("PRAGMA table_info(readings)").fetchall()]
            lines.append(f"readings table: {'present' if cols else 'MISSING'}")
            lines.append(f"gait columns: {'yes' if 'speed_log' in cols else 'NO'}")
            n = db.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
            lines.append(f"rows: {n}")
        except Exception as e:  # noqa: BLE001 - schema questions must not mask a working connection
            lines.append(f"schema check failed: {type(e).__name__}: {e}")
        db.close()
        return "\n".join(lines) + "\n", 200
    except Exception as e:  # noqa: BLE001 - reporting the failure IS this endpoint's job
        lines.append(f"FAILED after {time.time() - started:.2f}s")
        lines.append(f"{type(e).__name__}: {e}")
        return "\n".join(lines) + "\n", 503


# ----------------------------------------------------------------------------
# Language (English / Spanish). See i18n.py.
# ----------------------------------------------------------------------------
# Chosen per browser: ?lang=xx (the switch in every page header) sets a cookie
# that sticks; without one, the browser's own language decides, so a doctor
# whose browser is in Spanish lands on Spanish pages without touching anything.
LANG_COOKIE = "lang"


@app.before_request
def pick_language():
    asked = request.args.get("lang")
    if asked in i18n.LANGS:
        g.lang, g.lang_chosen = asked, True
        return
    cookie = request.cookies.get(LANG_COOKIE)
    g.lang = cookie if cookie in i18n.LANGS else (
        request.accept_languages.best_match(i18n.LANGS) or i18n.DEFAULT_LANG)


@app.after_request
def remember_language(resp):
    if g.get("lang_chosen"):
        resp.set_cookie(LANG_COOKIE, g.lang, max_age=365 * 24 * 3600,
                        samesite="Lax", secure=FORCE_HTTPS, httponly=True)
    return resp


def t(text, **kw):
    """`text` in the current request's language."""
    return i18n.t(text, g.get("lang", i18n.DEFAULT_LANG), **kw)


def lang_url(code):
    """The current page in another language, keeping its other query args."""
    args = request.args.to_dict()
    args["lang"] = code
    return url_for(request.endpoint, **(request.view_args or {}), **args)


@app.context_processor
def i18n_context():
    lang = g.get("lang", i18n.DEFAULT_LANG)
    return {
        "lang": lang,
        "locale": i18n.LOCALES[lang],
        "languages": i18n.SWITCH_NAMES,
        "lang_url": lang_url,
        "_": t,
        "csrf_token": csrf_token,
        "tz_name": i18n.DISPLAY_TZ_NAME,
        "alert_name": lambda code: i18n.alert_name(code, lang),
        "dt": lambda ms: i18n.fmt_ms(ms, lang),
    }


@app.template_filter("num")
def num_filter(value, digits=None):
    return i18n.num(value, g.get("lang", i18n.DEFAULT_LANG), digits)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        if login_blocked(ip):
            audit("login.throttled", actor=ip)
            flash(t("Too many attempts. Try again in a few minutes."))
            return render_template("login.html"), 429
        user = request.form.get("user", "")
        pw = request.form.get("password", "")
        if user == DOCTOR_USER and check_password_hash(DOCTOR_PW_HASH, pw):
            session.clear()             # a fresh session, and a fresh CSRF token with it
            session["user"] = user
            audit("login.success", actor=user)
            return redirect(safe_next(request.args.get("next")))
        login_failed(ip)
        audit("login.failure", target=user[:64], actor=ip)
        flash(t("Invalid credentials"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    audit("logout")
    session.clear()
    return redirect(url_for("login"))


# ----------------------------------------------------------------------------
# Ingest endpoint (phone POSTs here; token-authenticated)
# ----------------------------------------------------------------------------
def _opt_int(src, name):
    """A non-negative integer form field, or None when absent or unparseable.
    None, not 0: a missing measurement and a measured zero mean different
    things to every chart and alert downstream."""
    raw = (src.get(name) or "").strip()
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _gait_fields(src):
    """The optional gait half of an upload, normalised to all-None when the
    phone sent no usable gait block."""
    cycles = _opt_int(src, "gaitCycles")
    if not cycles:
        return dict.fromkeys(("speed_log", "cycles", "mean_speed", "stance_pct",
                              "stride_ms", "stride_cv", "stride_cm", "excursion_01"))
    return {
        "speed_log":    src.get("speedLog", "") or None,
        "cycles":       cycles,
        "mean_speed":   _opt_int(src, "meanSpeed"),
        "stance_pct":   _opt_int(src, "stancePct"),
        "stride_ms":    _opt_int(src, "strideMs"),
        "stride_cv":    _opt_int(src, "strideCv"),
        "stride_cm":    _opt_int(src, "strideCm"),
        "excursion_01": _opt_int(src, "excursion01"),
    }


# What the phone sends as sheetName: the device MAC with ':' written as '_',
# plus "_Battery" for the battery stream. It becomes part of page URLs and the
# key every table is joined on, so nothing else is accepted.
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")
# Capture times outside this range come from a phone whose clock is wrong. A
# date in the future would make the patient look freshly seen for ever.
MIN_CAPTURE_MS = 1_577_836_800_000          # 2020-01-01
MAX_CAPTURE_AHEAD_MS = 86_400_000


@app.route("/", methods=["POST"])
@app.route("/data", methods=["POST"])
def ingest():
    if INGEST_TOKEN and not token_ok(request.values.get("token", "")):
        audit("ingest.denied", actor=request.remote_addr)
        abort(401)

    src = request.form if request.form else request.args
    device_id = (src.get("sheetName") or "Unknown_Device").strip()
    if not DEVICE_ID_RE.match(device_id):
        return "Rejected: sheetName is not a device id\n", 400
    try:
        total_steps = int(float(src.get("steps", "0")))
    except ValueError:
        total_steps = 0
    minute_log = src.get("logData", "")
    capture_raw = src.get("captureTime", "")
    now_ms = int(time.time() * 1000)
    captured_at = int(capture_raw) if capture_raw.isdigit() else now_ms
    if not MIN_CAPTURE_MS <= captured_at <= now_ms + MAX_CAPTURE_AHEAD_MS:
        captured_at = now_ms

    # Gait block. Optional on purpose: a device on step-only firmware, or a day
    # with no walking long enough to analyse, posts none of these fields and
    # the reading is stored exactly as it always was.
    gait = _gait_fields(src)

    db = get_db()
    # Idempotent: the phone retries an upload whose response it never saw, and
    # on a sleeping free instance the server can commit the row well after the
    # client has given up waiting. A repeat of the same day is a no-op instead
    # of a second copy of it. Still a 200, so the phone clears its cache.
    #
    # The one thing a repeat may add is gait. The device resends a whole day
    # when the link drops between its step log and its gait log, so the same
    # day can arrive first without gait and then with it; the stored row takes
    # the gait it was missing and is otherwise left alone.
    db.execute(
        "INSERT INTO readings (device_id, captured_at, total_steps, minute_log, received_at,"
        " speed_log, gait_cycles, mean_speed, stance_pct, stride_ms, stride_cv,"
        " stride_cm, excursion_01)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (device_id, captured_at) DO UPDATE SET"
        " speed_log = excluded.speed_log, gait_cycles = excluded.gait_cycles,"
        " mean_speed = excluded.mean_speed, stance_pct = excluded.stance_pct,"
        " stride_ms = excluded.stride_ms, stride_cv = excluded.stride_cv,"
        " stride_cm = excluded.stride_cm, excursion_01 = excluded.excursion_01"
        " WHERE readings.gait_cycles IS NULL AND excluded.gait_cycles IS NOT NULL",
        (device_id, captured_at, total_steps, minute_log, now_ms,
         gait["speed_log"], gait["cycles"], gait["mean_speed"], gait["stance_pct"],
         gait["stride_ms"], gait["stride_cv"], gait["stride_cm"], gait["excursion_01"]),
    )
    db.commit()
    walked = f" ({gait['cycles']} gait cycles)" if gait["cycles"] else ""
    return f"Success: stored {total_steps} steps for {device_id}{walked}\n"


# ----------------------------------------------------------------------------
# Status / alert evaluation
# ----------------------------------------------------------------------------
def evaluate(db):
    s = get_settings(db)
    silent_hours = float(s["silent_hours"])
    battery_min = int(float(s.get("battery_min", ALERT_BATTERY_MIN)))
    configs = get_all_patient_configs(db)
    aliases = get_aliases(db)
    now_ms = int(time.time() * 1000)
    rows = db.execute(
        """
        SELECT r.device_id, r.captured_at, last.seen AS last_received,
               r.total_steps, r.minute_log,
               r.speed_log, r.gait_cycles, r.mean_speed, r.stance_pct,
               r.stride_ms, r.stride_cv, r.stride_cm, r.excursion_01
        FROM readings r
        JOIN (SELECT device_id, MAX(captured_at) AS m, MAX(received_at) AS seen
              FROM readings GROUP BY device_id) last
          ON r.device_id = last.device_id AND r.captured_at = last.m
        ORDER BY r.device_id
        """
    ).fetchall()

    # Latest battery % per patient, keyed by the parent MAC (strip "_Battery").
    battery = {r["device_id"][:-len(BATTERY_SUFFIX)]: r["total_steps"]
               for r in rows if r["device_id"].endswith(BATTERY_SUFFIX)}

    summary = []
    for r in rows:
        did = r["device_id"]
        if did.endswith(BATTERY_SUFFIX):
            continue                      # folded into its parent patient below
        cfg_p = configs.get(did, PATIENT_DEFAULTS)
        # None if this patient sends no battery stream, or the device could
        # not read its battery.
        bat = battery_state(battery.get(did))
        gait = gait_metrics(r)            # None when the day carries no gait block
        activity = day_activity(parse_log(r["minute_log"]), gait)
        cadence, active_minutes = activity["cadence"], activity["active_minutes"]

        # "Last seen" is when the server last heard from the device, not the
        # date of the newest day. The two differ whenever a day reaches the
        # phone late: it is dated back to when it ended, and judging silence
        # by that date would keep a patient SILENT who has just reported.
        seen_ms = r["last_received"]
        silent = (now_ms - seen_ms) > silent_hours * 3600_000
        # A threshold of 0 means "metric disabled" (no alert).
        low = cfg_p["min_steps"] > 0 and r["total_steps"] < cfg_p["min_steps"]
        # Like speed below: a day with no readable cadence is unknown, not slow.
        low_cadence = (cfg_p["min_cadence"] > 0 and cadence is not None
                       and cadence < cfg_p["min_cadence"])
        low_minutes = cfg_p["min_minutes"] > 0 and active_minutes < cfg_p["min_minutes"]
        # Only alert on a speed that was actually measured. A day with no gait
        # block is unknown, not slow -- SILENT already covers a device that has
        # stopped reporting, and raising SPEED here would fire on every patient
        # whose device runs step-only firmware.
        low_speed = (cfg_p["min_speed"] > 0 and gait is not None
                     and gait["speed_ms"] < cfg_p["min_speed"])
        battery_low = bat is not None and bat["soc"] < battery_min
        alerts = (["SILENT"] if silent else []) \
               + (["LOW"] if low else []) \
               + (["CADENCE"] if low_cadence else []) \
               + (["MINUTES"] if low_minutes else []) \
               + (["SPEED"] if low_speed else []) \
               + (["BATTERY"] if battery_low else [])
        summary.append({
            "device_id": did,
            "label": aliases.get(did) or did,
            "last_seen_ms": seen_ms,
            "hours_ago": round((now_ms - seen_ms) / 3600_000, 1),
            "total_steps": r["total_steps"],
            "cadence": cadence,
            "cadence_source": activity["cadence_source"],
            "active_minutes": active_minutes,
            "min_steps": cfg_p["min_steps"],
            "min_cadence": cfg_p["min_cadence"],
            "min_minutes": cfg_p["min_minutes"],
            "min_speed": cfg_p["min_speed"],
            "gait": gait,
            "battery": bat["soc"] if bat else None,       # estimated charge, %
            "battery_v": bat["volts"] if bat else None,
            "status": "ALERT" if alerts else "OK",
            "alerts": alerts,
        })
    return summary


# ----------------------------------------------------------------------------
# Doctor-only views
# ----------------------------------------------------------------------------
@app.route("/", methods=["GET"])
@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    summary = evaluate(db)
    cfg = get_settings(db)
    audit("view.dashboard")
    return render_template("dashboard.html", patients=summary,
                           silent_hours=cfg["silent_hours"], battery_min=cfg["battery_min"],
                           generated=i18n.fmt_ms(time.time() * 1000, g.lang))


@app.route("/patient/<device_id>")
@login_required
def patient(device_id):
    db = get_db()
    total_days = db.execute("SELECT COUNT(*) FROM readings WHERE device_id = ?",
                            (device_id,)).fetchone()[0]
    if not total_days:
        abort(404)
    # The most recent PATIENT_DAYS by default; ?days=N or ?days=all for more.
    asked = request.args.get("days", "")
    limit = total_days if asked == "all" else (
        int(asked) if asked.isdigit() and int(asked) > 0 else PATIENT_DAYS)
    rows = db.execute(
        "SELECT captured_at, total_steps, minute_log, speed_log, gait_cycles,"
        " mean_speed, stance_pct, stride_ms, stride_cv, stride_cm, excursion_01"
        " FROM readings WHERE device_id = ? ORDER BY captured_at DESC LIMIT ?",
        (device_id, limit)).fetchall()[::-1]            # back to oldest first
    audit("view.patient", target=device_id)

    daily_labels = [i18n.fmt_ms(r["captured_at"], g.lang, day_only=True) for r in rows]
    daily_steps = [r["total_steps"] for r in rows]
    daily_minutes = [parse_log(r["minute_log"]) for r in rows]   # one per-minute array per day
    # Clock time of each log's first minute, so the charts show when the
    # patient actually walked rather than assuming the log starts at midnight.
    daily_start = [log_start_minute(r["captured_at"], len(m))
                   for r, m in zip(rows, daily_minutes)]
    daily_speed = [speed_series(r["speed_log"]) for r in rows]   # m/s, None where unmeasured
    daily_gait = [gait_metrics(r) for r in rows]                 # None on days without gait
    # Bouts, active minutes and the day's one cadence figure, per day.
    daily_activity = [day_activity(m, g) for m, g in zip(daily_minutes, daily_gait)]
    me = next((p for p in evaluate(db) if p["device_id"] == device_id), None)
    label = get_aliases(db).get(device_id) or device_id

    return render_template("patient.html", device_id=device_id, label=label,
                           daily_labels=daily_labels, daily_steps=daily_steps,
                           daily_minutes=daily_minutes, daily_speed=daily_speed,
                           daily_start=daily_start,
                           daily_gait=daily_gait, daily_activity=daily_activity,
                           bout_labels=bout_labels(g.lang), me=me,
                           shown_days=len(rows), total_days=total_days)


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    db = get_db()
    if request.method == "POST":
        form = request.form
        values = {
            "smtp_host":    form.get("smtp_host", "").strip(),
            "smtp_port":    form.get("smtp_port", "587").strip() or "587",
            "smtp_user":    form.get("smtp_user", "").strip(),
            "alert_from":   form.get("alert_from", "").strip(),
            "alert_to":     form.get("alert_to", "").strip(),
            "silent_hours": form.get("silent_hours", "26").strip() or "26",
            "battery_min":  form.get("battery_min", "20").strip() or "20",
            "alert_lang":   i18n.norm_lang(form.get("alert_lang", ALERT_LANG)),
        }
        # Only overwrite the password when a new one is typed (field left blank = keep).
        if form.get("smtp_pass", ""):
            values["smtp_pass"] = form.get("smtp_pass")
        save_settings(db, values)
        audit("settings.update")
        flash(t("Settings saved"))
        return redirect(url_for("settings_page"))

    cfg = get_settings(db)
    aliases = get_aliases(db)
    devices = [{"device_id": d, "label": aliases.get(d, "")} for d in known_devices(db)]
    return render_template("settings.html", cfg=cfg, has_pass=bool(cfg.get("smtp_pass")),
                           devices=devices, language_names=i18n.LANGUAGE_NAMES)


@app.route("/settings/labels", methods=["POST"])
@login_required
def settings_labels():
    """Save the MAC -> label map. Blank clears a label (device shows its MAC)."""
    db = get_db()
    for did in known_devices(db):
        label = request.form.get("label_" + did, "").strip()
        if label:
            db.execute("INSERT INTO device_alias (device_id, label) VALUES (?, ?)"
                       " ON CONFLICT(device_id) DO UPDATE SET label = excluded.label",
                       (did, label))
        else:
            db.execute("DELETE FROM device_alias WHERE device_id = ?", (did,))
    db.commit()
    audit("settings.labels")
    flash(t("Patient labels saved."))
    return redirect(url_for("settings_page"))


@app.route("/settings/test", methods=["POST"])
@login_required
def settings_test():
    cfg = get_settings(get_db())
    # The test goes out in the alerts' language, so it previews what doctors get;
    # the result is reported in the language of the page.
    subject, body = i18n.email("TEST", cfg.get("alert_lang"))
    ok, msg = send_email(subject, body, cfg, lang=g.lang)
    audit("settings.test", target=("ok" if ok else "fail"))
    flash(t("Test email sent: {msg}", msg=msg) if ok else t("Test failed: {msg}", msg=msg))
    return redirect(url_for("settings_page"))


@app.route("/patient/<device_id>/config", methods=["GET", "POST"])
@login_required
def patient_config_page(device_id):
    """Per-patient activity thresholds: minimum steps/day, minimum cadence
    (steps per active minute) and minimum active minutes/day. 0 disables a metric."""
    db = get_db()
    if not db.execute("SELECT 1 FROM readings WHERE device_id = ? LIMIT 1",
                      (device_id,)).fetchone():
        abort(404)

    if request.method == "POST":
        def num(name, cast):
            raw = request.form.get(name, "").strip()
            v = cast(raw) if raw else 0
            if v < 0:
                raise ValueError
            return v
        try:
            min_steps   = num("min_steps", lambda x: int(float(x)))
            min_cadence = num("min_cadence", float)
            min_minutes = num("min_minutes", lambda x: int(float(x)))
            min_speed   = num("min_speed", float)
        except ValueError:
            flash(t("Invalid value — thresholds must be numbers ≥ 0 (0 disables a metric)."))
            return redirect(url_for("patient_config_page", device_id=device_id))
        db.execute(
            "INSERT INTO patient_config (device_id, min_steps, min_cadence, min_minutes, min_speed)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(device_id) DO UPDATE SET"
            " min_steps=excluded.min_steps, min_cadence=excluded.min_cadence,"
            " min_minutes=excluded.min_minutes, min_speed=excluded.min_speed",
            (device_id, min_steps, min_cadence, min_minutes, min_speed))
        db.commit()
        audit("patient.config", target=f"{device_id} steps={min_steps} cadence={min_cadence}"
                                       f" minutes={min_minutes} speed={min_speed}")
        flash(t("Thresholds saved."))
        return redirect(url_for("patient_config_page", device_id=device_id))

    cfg_p = get_patient_config(db, device_id)
    return render_template("patient_config.html", device_id=device_id, cfg=cfg_p)


@app.route("/patient/<device_id>/delete", methods=["POST"])
@login_required
def delete_patient(device_id):
    """GDPR right to erasure: remove all data for a device/pseudonym,
    including its battery stream and per-patient configuration."""
    db = get_db()
    # Not `t`: that name is the translation function used a few lines down,
    # and shadowing it made every deletion end in a 500 after the data was gone.
    for stream in (device_id, device_id + BATTERY_SUFFIX):
        db.execute("DELETE FROM readings WHERE device_id = ?", (stream,))
        db.execute("DELETE FROM alert_state WHERE device_id = ?", (stream,))
    db.execute("DELETE FROM patient_config WHERE device_id = ?", (device_id,))
    db.execute("DELETE FROM device_alias WHERE device_id = ?", (device_id,))
    db.commit()
    audit("delete.patient", target=device_id)
    flash(t("All data for {device} deleted", device=device_id))
    return redirect(url_for("dashboard"))


# ----------------------------------------------------------------------------
# Alerting (background thread)
# ----------------------------------------------------------------------------
def send_email(subject, body, cfg, lang="en"):
    """Send one email using the dashboard-configured SMTP settings (cfg dict).
    Returns (ok, message) so the Settings 'send test' button can report back;
    `lang` is the language of that message, not of the email."""
    if not cfg.get("smtp_host") or not cfg.get("alert_to"):
        msg = i18n.t("Email not configured (set SMTP host and recipient in Settings).", lang)
        app.logger.info("[alert] %s", msg)
        return False, msg
    try:
        m = EmailMessage()
        m["Subject"] = subject
        m["From"] = cfg.get("alert_from") or cfg.get("smtp_user")
        m["To"] = cfg["alert_to"]
        m.set_content(body)
        port = int(cfg.get("smtp_port") or 587)
        # 465 is TLS from the first byte; every other port upgrades with STARTTLS.
        smtp = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with smtp(cfg["smtp_host"], port, timeout=20) as srv:
            if port != 465:
                srv.starttls()
            if cfg.get("smtp_user"):
                srv.login(cfg["smtp_user"], cfg.get("smtp_pass", ""))
            srv.send_message(m)
        app.logger.info("[alert] emailed: %s", subject)
        return True, i18n.t("Sent to {to}", lang, to=cfg["alert_to"])
    except Exception as e:  # noqa: BLE001
        app.logger.error("[alert] email failed: %s", e)
        return False, str(e)


def check_alerts():
    ensure_schema()
    db = connect()
    now_ms = int(time.time() * 1000)
    cooldown_ms = ALERT_COOLDOWN_H * 3600_000
    cfg = get_settings(db)
    lang = i18n.norm_lang(cfg.get("alert_lang"))
    num = lambda v: i18n.num(v, lang)          # noqa: E731
    try:
        if AUDIT_RETENTION_DAYS > 0:
            db.execute("DELETE FROM audit WHERE ts < ?",
                       (now_ms - AUDIT_RETENTION_DAYS * 86_400_000,))
        for p in evaluate(db):
            for kind in p["alerts"]:
                row = db.execute("SELECT notified_at FROM alert_state WHERE device_id=? AND kind=?",
                                 (p["device_id"], kind)).fetchone()
                if row and (now_ms - row["notified_at"]) < cooldown_ms:
                    continue
                gait = p["gait"] or {}
                subject, body = i18n.email(
                    kind if kind in i18n.EMAILS else "LOW", lang,
                    patient=p["device_id"], hours=num(p["hours_ago"]),
                    silent_hours=num(cfg["silent_hours"]),
                    last_seen=i18n.fmt_ms(p["last_seen_ms"], lang),
                    battery=p["battery"], battery_min=num(cfg["battery_min"]),
                    cadence=p["cadence"], min_cadence=num(p["min_cadence"]),
                    cadence_source=i18n.t("measured from gait" if p["cadence_source"] == "gait"
                                          else "estimated from step counts", lang),
                    speed=num(gait.get("speed_ms")), min_speed=num(p["min_speed"]),
                    cycles=gait.get("cycles"), stance=gait.get("stance_pct"),
                    gait_cadence=gait.get("cadence"),
                    active_minutes=p["active_minutes"], min_minutes=p["min_minutes"],
                    steps=p["total_steps"], min_steps=p["min_steps"])
                ok, _ = send_email(subject, body, cfg)
                if ok:
                    # Only start the cooldown once an alert has actually been delivered,
                    # so a not-yet-configured mailer doesn't suppress the first real alert.
                    db.execute(
                        "INSERT INTO alert_state (device_id, kind, notified_at) VALUES (?,?,?)"
                        " ON CONFLICT(device_id, kind) DO UPDATE SET notified_at=excluded.notified_at",
                        (p["device_id"], kind, now_ms))
            for kind in ("SILENT", "LOW", "CADENCE", "MINUTES", "SPEED", "BATTERY"):
                if kind not in p["alerts"]:
                    db.execute("DELETE FROM alert_state WHERE device_id=? AND kind=?",
                               (p["device_id"], kind))
        db.commit()
    finally:
        db.close()


def alert_loop():
    while True:
        try:
            check_alerts()          # run once at startup, then every ALERT_CHECK_MIN
        except Exception as e:  # noqa: BLE001
            app.logger.error("[alert] check failed: %s", e)
        time.sleep(ALERT_CHECK_MIN * 60)


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
# The schema is applied lazily rather than at import, and an import-time failure
# is never fatal.
#
# init_schema() talks to the database, so having it on the import path meant a
# worker could not start at all while the database was unreachable: gunicorn
# would restart it straight back into the same wall, /healthz would stop
# answering, and the service went from "degraded" to "gone" with no way to see
# why. Booting regardless keeps the health endpoint and the logs available,
# which are exactly what you need when the database is the thing that broke --
# and the first request that reaches a working database applies the schema.
try:
    ensure_schema()
except Exception as e:  # noqa: BLE001 - a dead database must not stop the app booting
    app.logger.error("[schema] not applied at startup, will retry on first request: %s", e)

if os.environ.get("RUN_ALERTER", "1") == "1":
    threading.Thread(target=alert_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
