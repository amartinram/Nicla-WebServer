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
import time
import smtplib
import secrets
import threading
from functools import wraps
from email.message import EmailMessage
from datetime import datetime

from flask import (Flask, request, render_template, redirect, url_for,
                   session, g, abort, flash)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

from db import connect, init_schema

# ----------------------------------------------------------------------------
# Configuration (override with environment variables)
# ----------------------------------------------------------------------------
# Storage is selected in db.py: DATABASE_URL (Postgres, production) else
# DB_PATH (SQLite, local development).
ALERT_SILENT_HOURS = float(os.environ.get("ALERT_SILENT_HOURS", "26"))
ALERT_MIN_STEPS    = int(os.environ.get("ALERT_MIN_STEPS", "1000"))   # per-patient default/fallback
ALERT_MIN_CADENCE  = float(os.environ.get("ALERT_MIN_CADENCE", "0"))  # per-patient default (0 = off)
ALERT_MIN_MINUTES  = int(os.environ.get("ALERT_MIN_MINUTES", "0"))    # per-patient default (0 = off)
ALERT_BATTERY_MIN  = int(os.environ.get("ALERT_BATTERY_MIN", "20"))   # global battery % floor
ALERT_CHECK_MIN    = float(os.environ.get("ALERT_CHECK_MIN", "30"))
ALERT_COOLDOWN_H   = float(os.environ.get("ALERT_COOLDOWN_H", "12"))

# Battery arrives as a separate stream "<MAC>_Battery" whose "steps" value is the
# battery percentage. We fold it back into its parent patient (see evaluate()).
BATTERY_SUFFIX = "_Battery"

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
# Measured on Render: the chain arrives as "<client>, <internal router>", so
# the client is the 2nd entry from the right. The rightmost is an internal
# 10.x address that changes per request, which is what made every audit row
# read 10.30.151.133 before this was raised from 1 to 2.
PROXY_HOPS = int(os.environ.get("PROXY_HOPS", "2"))
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXY_HOPS, x_proto=1, x_host=1)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=FORCE_HTTPS,
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
def get_db():
    if "db" not in g:
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
                    "min_minutes": ALERT_MIN_MINUTES}


def _row_to_config(row):
    return {"min_steps": int(row[0]), "min_cadence": float(row[1]), "min_minutes": int(row[2])}


def get_patient_config(db, device_id):
    """This patient's thresholds, falling back to PATIENT_DEFAULTS when unset."""
    row = db.execute("SELECT min_steps, min_cadence, min_minutes FROM patient_config"
                     " WHERE device_id = ?", (device_id,)).fetchone()
    return _row_to_config(row) if row else dict(PATIENT_DEFAULTS)


def get_all_patient_configs(db):
    return {r[0]: _row_to_config(r[1:]) for r in
            db.execute("SELECT device_id, min_steps, min_cadence, min_minutes"
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


def activity_metrics(minute_log):
    """(avg_cadence over active minutes, count of active minutes) from a
    comma-separated per-minute step log."""
    vals = [int(x) for x in (minute_log or "").split(",") if x.strip().lstrip("-").isdigit()]
    active = [v for v in vals if v > 0]
    avg_cadence = round(sum(active) / len(active), 1) if active else 0
    return avg_cadence, len(active)


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


@app.route(HEALTH_PATH)
def healthz():
    """Liveness only — deliberately does not touch the database. The app and the
    database sleep independently; failing this check on a cold database would
    make the host restart a perfectly healthy web service."""
    return "ok\n", 200


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
        user = request.form.get("user", "")
        pw = request.form.get("password", "")
        if user == DOCTOR_USER and check_password_hash(DOCTOR_PW_HASH, pw):
            session["user"] = user
            audit("login.success", actor=user)
            nxt = request.args.get("next") or url_for("dashboard")
            return redirect(nxt)
        audit("login.failure", target=user, actor=request.remote_addr)
        flash("Invalid credentials")
    return render_template("login.html")


@app.route("/logout")
def logout():
    audit("logout")
    session.clear()
    return redirect(url_for("login"))


# ----------------------------------------------------------------------------
# Ingest endpoint (phone POSTs here; token-authenticated)
# ----------------------------------------------------------------------------
@app.route("/", methods=["POST"])
@app.route("/data", methods=["POST"])
def ingest():
    if INGEST_TOKEN:
        token = request.values.get("token", "")
        if not secrets.compare_digest(token, INGEST_TOKEN):
            audit("ingest.denied", actor=request.remote_addr)
            abort(401)

    src = request.form if request.form else request.args
    device_id = (src.get("sheetName") or "Unknown_Device").strip()
    try:
        total_steps = int(float(src.get("steps", "0")))
    except ValueError:
        total_steps = 0
    minute_log = src.get("logData", "")
    capture_raw = src.get("captureTime", "")
    captured_at = int(capture_raw) if capture_raw.isdigit() else int(time.time() * 1000)

    db = get_db()
    db.execute(
        "INSERT INTO readings (device_id, captured_at, total_steps, minute_log, received_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (device_id, captured_at, total_steps, minute_log, int(time.time() * 1000)),
    )
    db.commit()
    return f"Success: stored {total_steps} steps for {device_id}\n"


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
        SELECT r.device_id, r.captured_at, r.total_steps, r.minute_log
        FROM readings r
        JOIN (SELECT device_id, MAX(captured_at) AS m
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
        bat = battery.get(did)            # None if this patient sends no battery stream
        avg_cadence, active_minutes = activity_metrics(r["minute_log"])

        silent = (now_ms - r["captured_at"]) > silent_hours * 3600_000
        # A threshold of 0 means "metric disabled" (no alert).
        low = cfg_p["min_steps"] > 0 and r["total_steps"] < cfg_p["min_steps"]
        low_cadence = cfg_p["min_cadence"] > 0 and avg_cadence < cfg_p["min_cadence"]
        low_minutes = cfg_p["min_minutes"] > 0 and active_minutes < cfg_p["min_minutes"]
        battery_low = bat is not None and bat < battery_min
        alerts = (["SILENT"] if silent else []) \
               + (["LOW"] if low else []) \
               + (["CADENCE"] if low_cadence else []) \
               + (["MINUTES"] if low_minutes else []) \
               + (["BATTERY"] if battery_low else [])
        summary.append({
            "device_id": did,
            "label": aliases.get(did) or did,
            "last_seen_ms": r["captured_at"],
            "last_seen": datetime.fromtimestamp(r["captured_at"] / 1000).strftime("%Y-%m-%d %H:%M"),
            "hours_ago": round((now_ms - r["captured_at"]) / 3600_000, 1),
            "total_steps": r["total_steps"],
            "avg_cadence": avg_cadence,
            "active_minutes": active_minutes,
            "min_steps": cfg_p["min_steps"],
            "min_cadence": cfg_p["min_cadence"],
            "min_minutes": cfg_p["min_minutes"],
            "battery": bat,
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
                           generated=datetime.now().strftime("%Y-%m-%d %H:%M"))


@app.route("/patient/<device_id>")
@login_required
def patient(device_id):
    db = get_db()
    rows = db.execute(
        "SELECT captured_at, total_steps, minute_log FROM readings"
        " WHERE device_id = ? ORDER BY captured_at", (device_id,)).fetchall()
    if not rows:
        abort(404)
    audit("view.patient", target=device_id)

    def parse_log(s):
        return [int(x) for x in (s or "").split(",") if x.strip().lstrip("-").isdigit()]

    daily_labels = [datetime.fromtimestamp(r["captured_at"] / 1000).strftime("%m-%d %H:%M") for r in rows]
    daily_steps = [r["total_steps"] for r in rows]
    daily_minutes = [parse_log(r["minute_log"]) for r in rows]   # one per-minute array per day
    last = rows[-1]
    avg_cadence, active_minutes = activity_metrics(last["minute_log"])
    me = next((p for p in evaluate(db) if p["device_id"] == device_id), None)
    label = get_aliases(db).get(device_id) or device_id

    return render_template("patient.html", device_id=device_id, label=label,
                           daily_labels=daily_labels, daily_steps=daily_steps,
                           daily_minutes=daily_minutes, avg_cadence=avg_cadence,
                           active_minutes=active_minutes, me=me)


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
        }
        # Only overwrite the password when a new one is typed (field left blank = keep).
        if form.get("smtp_pass", ""):
            values["smtp_pass"] = form.get("smtp_pass")
        save_settings(db, values)
        audit("settings.update")
        flash("Settings saved")
        return redirect(url_for("settings_page"))

    cfg = get_settings(db)
    aliases = get_aliases(db)
    devices = [{"device_id": d, "label": aliases.get(d, "")} for d in known_devices(db)]
    return render_template("settings.html", cfg=cfg, has_pass=bool(cfg.get("smtp_pass")),
                           devices=devices)


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
    flash("Patient labels saved.")
    return redirect(url_for("settings_page"))


@app.route("/settings/test", methods=["POST"])
@login_required
def settings_test():
    cfg = get_settings(get_db())
    ok, msg = send_email("[StepCounter] Test email",
                         "This is a test from the StepCounter server. "
                         "If you can read this, patient-failure alerts will reach this inbox.",
                         cfg)
    audit("settings.test", target=("ok" if ok else "fail"))
    flash(("Test email sent: " if ok else "Test failed: ") + msg)
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
        except ValueError:
            flash("Invalid value — thresholds must be numbers ≥ 0 (0 disables a metric).")
            return redirect(url_for("patient_config_page", device_id=device_id))
        db.execute(
            "INSERT INTO patient_config (device_id, min_steps, min_cadence, min_minutes)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(device_id) DO UPDATE SET"
            " min_steps=excluded.min_steps, min_cadence=excluded.min_cadence,"
            " min_minutes=excluded.min_minutes",
            (device_id, min_steps, min_cadence, min_minutes))
        db.commit()
        audit("patient.config", target=f"{device_id} steps={min_steps} cadence={min_cadence} minutes={min_minutes}")
        flash("Thresholds saved.")
        return redirect(url_for("patient_config_page", device_id=device_id))

    cfg_p = get_patient_config(db, device_id)
    return render_template("patient_config.html", device_id=device_id, cfg=cfg_p)


@app.route("/patient/<device_id>/delete", methods=["POST"])
@login_required
def delete_patient(device_id):
    """GDPR right to erasure: remove all data for a device/pseudonym,
    including its battery stream and per-patient configuration."""
    db = get_db()
    targets = (device_id, device_id + BATTERY_SUFFIX)
    for t in targets:
        db.execute("DELETE FROM readings WHERE device_id = ?", (t,))
        db.execute("DELETE FROM alert_state WHERE device_id = ?", (t,))
    db.execute("DELETE FROM patient_config WHERE device_id = ?", (device_id,))
    db.execute("DELETE FROM device_alias WHERE device_id = ?", (device_id,))
    db.commit()
    audit("delete.patient", target=device_id)
    flash(f"All data for {device_id} deleted")
    return redirect(url_for("dashboard"))


# ----------------------------------------------------------------------------
# Alerting (background thread)
# ----------------------------------------------------------------------------
def send_email(subject, body, cfg):
    """Send one email using the dashboard-configured SMTP settings (cfg dict).
    Returns (ok, message) so the Settings 'send test' button can report back."""
    if not cfg.get("smtp_host") or not cfg.get("alert_to"):
        msg = "Email not configured (set SMTP host and recipient in Settings)."
        app.logger.info("[alert] %s", msg)
        return False, msg
    try:
        m = EmailMessage()
        m["Subject"] = subject
        m["From"] = cfg.get("alert_from") or cfg.get("smtp_user")
        m["To"] = cfg["alert_to"]
        m.set_content(body)
        with smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port") or 587), timeout=20) as srv:
            srv.starttls()
            if cfg.get("smtp_user"):
                srv.login(cfg["smtp_user"], cfg.get("smtp_pass", ""))
            srv.send_message(m)
        app.logger.info("[alert] emailed: %s", subject)
        return True, f"Sent to {cfg['alert_to']}"
    except Exception as e:  # noqa: BLE001
        app.logger.error("[alert] email failed: %s", e)
        return False, str(e)


def check_alerts():
    db = connect()
    now_ms = int(time.time() * 1000)
    cooldown_ms = ALERT_COOLDOWN_H * 3600_000
    cfg = get_settings(db)
    try:
        for p in evaluate(db):
            for kind in p["alerts"]:
                row = db.execute("SELECT notified_at FROM alert_state WHERE device_id=? AND kind=?",
                                 (p["device_id"], kind)).fetchone()
                if row and (now_ms - row["notified_at"]) < cooldown_ms:
                    continue
                if kind == "SILENT":
                    subject = f"[StepCounter] Alert: patient {p['device_id']} has not walked"
                    body = (f"Patient: {p['device_id']}\n\n"
                            f"This patient has not walked (no data received) for "
                            f"{p['hours_ago']} h, exceeding the configured limit of "
                            f"{cfg['silent_hours']} h.\n"
                            f"Last data received: {p['last_seen']}.\n\n"
                            f"The device may be unworn, the battery empty, or the patient "
                            f"immobile.")
                elif kind == "BATTERY":
                    subject = f"[StepCounter] Alert: patient {p['device_id']} low battery"
                    body = (f"Patient: {p['device_id']}\n\n"
                            f"The device battery is at {p['battery']}%, below the configured "
                            f"threshold of {cfg['battery_min']}%.\n"
                            f"Last data received: {p['last_seen']}.\n\n"
                            f"The device should be recharged soon to avoid a gap in monitoring.")
                elif kind == "CADENCE":
                    subject = f"[StepCounter] Alert: patient {p['device_id']} low cadence"
                    body = (f"Patient: {p['device_id']}\n\n"
                            f"Average walking cadence was {p['avg_cadence']} steps/min on the "
                            f"latest day, below this patient's threshold of {p['min_cadence']} "
                            f"steps/min.\n"
                            f"Last data received: {p['last_seen']}.")
                elif kind == "MINUTES":
                    subject = f"[StepCounter] Alert: patient {p['device_id']} low active time"
                    body = (f"Patient: {p['device_id']}\n\n"
                            f"This patient was active for only {p['active_minutes']} minutes on "
                            f"the latest day, below this patient's threshold of "
                            f"{p['min_minutes']} minutes.\n"
                            f"Last data received: {p['last_seen']}.")
                else:
                    subject = f"[StepCounter] Alert: patient {p['device_id']} low activity"
                    body = (f"Patient: {p['device_id']}\n\n"
                            f"This patient has taken fewer steps than required: "
                            f"{p['total_steps']} steps on the latest day, below this patient's "
                            f"threshold of {p['min_steps']} steps.\n"
                            f"Last data received: {p['last_seen']}.")
                ok, _ = send_email(subject, body, cfg)
                if ok:
                    # Only start the cooldown once an alert has actually been delivered,
                    # so a not-yet-configured mailer doesn't suppress the first real alert.
                    db.execute(
                        "INSERT INTO alert_state (device_id, kind, notified_at) VALUES (?,?,?)"
                        " ON CONFLICT(device_id, kind) DO UPDATE SET notified_at=excluded.notified_at",
                        (p["device_id"], kind, now_ms))
            for kind in ("SILENT", "LOW", "CADENCE", "MINUTES", "BATTERY"):
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
init_schema(SETTINGS_DEFAULTS)
if os.environ.get("RUN_ALERTER", "1") == "1":
    threading.Thread(target=alert_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
