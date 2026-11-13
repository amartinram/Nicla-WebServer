"""
Server tests. No extra dependencies:

    python -m unittest discover -s tests -v

Runs against a throwaway SQLite file, never the real database: DATABASE_URL is
removed from the environment before the app is imported.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ.update(
    DB_PATH=str(Path(_TMP.name) / "test.db"),
    RUN_ALERTER="0",            # no background thread, no emails
    FORCE_HTTPS="0",
    INGEST_TOKEN="tok",
    DOCTOR_PASSWORD="pw",
    DISPLAY_TZ="Europe/Madrid",
    PATIENT_DAYS="3",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as server  # noqa: E402 - must follow the environment setup above
import i18n  # noqa: E402
from db import connect  # noqa: E402

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
MAC = "AA_BB_CC_DD_EE_01"


def now_ms():
    return int(time.time() * 1000)


def day_csv(steps_at=None):
    log = [0] * 1440
    for minute, steps in (steps_at or {}).items():
        log[minute] = steps
    return ",".join(map(str, log))


class ServerTest(unittest.TestCase):

    def setUp(self):
        db = connect()
        for table in ("readings", "alert_state", "audit", "patient_config", "device_alias"):
            db.execute(f"DELETE FROM {table}")
        db.commit()
        db.close()
        server._login_failures.clear()
        self.client = server.app.test_client()

    # -- helpers ------------------------------------------------------------
    def post_day(self, captured=None, device=MAC, token="tok", steps="120", **extra):
        data = {"sheetName": device, "steps": steps,
                "logData": day_csv({600: 60, 601: 60}),
                "captureTime": str(captured if captured is not None else now_ms())}
        data.update(extra)
        return self.client.post(f"/data?token={token}", data=data)

    def login(self):
        r = self.client.post("/login", data={"user": "doctor", "password": "pw"})
        self.assertEqual(r.status_code, 302)

    def csrf(self):
        with self.client.session_transaction() as s:
            s.setdefault("csrf", "test-csrf")
            return s["csrf"]

    def rows(self, device=MAC):
        db = connect()
        try:
            return db.execute("SELECT * FROM readings WHERE device_id = ? ORDER BY captured_at",
                              (device,)).fetchall()
        finally:
            db.close()

    def evaluate(self):
        db = connect()
        try:
            return {p["device_id"]: p for p in server.evaluate(db)}
        finally:
            db.close()

    # -- ingest -------------------------------------------------------------
    def test_ingest_needs_the_token(self):
        self.assertEqual(self.post_day(token="wrong").status_code, 401)
        self.assertEqual(self.rows(), [])

    def test_a_non_ascii_token_is_refused_not_a_crash(self):
        self.assertEqual(self.post_day(token="ñ").status_code, 401)

    def test_ingest_stores_a_day(self):
        self.assertEqual(self.post_day().status_code, 200)
        (row,) = self.rows()
        self.assertEqual(row["total_steps"], 120)
        self.assertIsNone(row["gait_cycles"])

    def test_a_retried_upload_is_not_a_second_day(self):
        t = now_ms()
        self.assertEqual(self.post_day(captured=t).status_code, 200)
        self.assertEqual(self.post_day(captured=t).status_code, 200)
        self.assertEqual(len(self.rows()), 1)

    def test_a_resent_day_adds_the_gait_it_was_missing(self):
        t = now_ms()
        self.post_day(captured=t)
        gait = dict(speedLog=day_csv({600: 62}), gaitCycles="12", meanSpeed="62",
                    stancePct="58", strideMs="1400", strideCv="30", strideCm="110",
                    excursion01="610")
        self.assertEqual(self.post_day(captured=t, **gait).status_code, 200)
        (row,) = self.rows()
        self.assertEqual(row["gait_cycles"], 12)
        self.assertEqual(row["stride_ms"], 1400)
        self.assertEqual(row["total_steps"], 120)          # the step half is untouched

    def test_a_resend_never_strips_gait_already_stored(self):
        t = now_ms()
        self.post_day(captured=t, speedLog=day_csv({600: 62}), gaitCycles="12", strideMs="1400")
        self.post_day(captured=t)                            # same day, no gait this time
        self.assertEqual(self.rows()[0]["gait_cycles"], 12)

    def test_ingest_refuses_a_device_id_that_is_not_one(self):
        self.assertEqual(self.post_day(device="../../etc/passwd").status_code, 400)
        self.assertEqual(self.post_day(device="<script>").status_code, 400)

    def test_a_capture_time_from_a_broken_clock_is_replaced(self):
        future = now_ms() + 400 * DAY_MS
        self.assertEqual(self.post_day(captured=future).status_code, 200)
        self.assertLess(abs(self.rows()[0]["captured_at"] - now_ms()), 60_000)

    def test_an_oversized_upload_is_refused(self):
        r = self.client.post("/data?token=tok",
                             data={"sheetName": MAC, "steps": "1", "logData": "9," * 700_000})
        self.assertEqual(r.status_code, 413)

    # -- battery ------------------------------------------------------------
    def test_the_gauge_is_voltage_not_charge(self):
        full, nominal, flat = (server.battery_state(g) for g in (98, 88, 84))
        self.assertGreaterEqual(full["soc"], 90)
        self.assertAlmostEqual(nominal["volts"], 3.74, places=2)
        self.assertLess(nominal["soc"], 30)                  # "88 %" is nearly empty
        self.assertLess(flat["soc"], 10)
        # Charge never rises as the gauge falls.
        socs = [server.battery_state(g)["soc"] for g in range(98, 58, -2)]
        self.assertEqual(socs, sorted(socs, reverse=True))

    def test_an_unreadable_battery_is_unknown(self):
        for raw in (None, 0, 254, 255, -1):
            self.assertIsNone(server.battery_state(raw))

    def test_low_battery_alert_fires_on_estimated_charge(self):
        self.post_day()
        self.post_day(device=MAC + "_Battery", steps="86")   # ~3.65 V
        p = self.evaluate()[MAC]
        self.assertIn("BATTERY", p["alerts"])
        self.assertLess(p["battery"], 20)

        self.post_day(device=MAC + "_Battery", steps="96", captured=now_ms() + 1000)
        self.assertNotIn("BATTERY", self.evaluate()[MAC]["alerts"])

    # -- alerts -------------------------------------------------------------
    def test_a_day_that_arrived_late_is_not_silence(self):
        # Ended 40 h ago, reached the server just now: the device is alive.
        self.post_day(captured=now_ms() - 40 * HOUR_MS)
        p = self.evaluate()[MAC]
        self.assertNotIn("SILENT", p["alerts"])
        self.assertLess(p["hours_ago"], 1)

    def test_no_data_for_longer_than_the_limit_is_silence(self):
        old = now_ms() - 40 * HOUR_MS
        db = connect()
        db.execute("INSERT INTO readings (device_id, captured_at, total_steps, minute_log,"
                   " received_at) VALUES (?, ?, ?, ?, ?)", (MAC, old, 5000, day_csv(), old))
        db.commit()
        db.close()
        self.assertIn("SILENT", self.evaluate()[MAC]["alerts"])

    # -- time ---------------------------------------------------------------
    def test_a_log_starts_when_the_device_day_started_not_at_midnight(self):
        # 2026-07-01 10:30 UTC is 12:30 in Madrid (summer time).
        ended = 1_782_901_800_000
        self.assertEqual(i18n.local_dt(ended).strftime("%H:%M"), "12:30")
        self.assertEqual(server.log_start_minute(ended, 1440), 12 * 60 + 30)
        self.assertEqual(server.log_start_minute(ended, 90), 11 * 60)

    def test_times_are_shown_in_the_display_zone(self):
        winter = 1_768_473_000_000                           # 2026-01-15 10:30 UTC
        self.assertEqual(i18n.fmt_ms(winter, "es"), "15/01/2026 11:30")

    # -- access control -----------------------------------------------------
    def test_pages_need_a_login(self):
        self.post_day()
        for path in ("/", f"/patient/{MAC}", "/settings"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/login", r.headers["Location"])

    def test_login_does_not_redirect_off_site(self):
        for target in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
            r = self.client.post("/login", query_string={"next": target},
                                 data={"user": "doctor", "password": "pw"})
            self.assertEqual(r.status_code, 302)
            self.assertNotIn("evil.example", r.headers["Location"])
            self.client.get("/logout")

        r = self.client.post("/login", query_string={"next": f"/patient/{MAC}"},
                             data={"user": "doctor", "password": "pw"})
        self.assertTrue(r.headers["Location"].endswith(f"/patient/{MAC}"))

    def test_repeated_wrong_passwords_are_throttled(self):
        for _ in range(server.LOGIN_MAX_FAILURES):
            r = self.client.post("/login", data={"user": "doctor", "password": "no"})
            self.assertEqual(r.status_code, 200)
        r = self.client.post("/login", data={"user": "doctor", "password": "pw"})
        self.assertEqual(r.status_code, 429)

    def test_a_form_post_without_the_csrf_token_is_refused(self):
        self.post_day()
        self.login()
        r = self.client.post(f"/patient/{MAC}/delete")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.rows()), 1)

        r = self.client.post(f"/patient/{MAC}/delete", data={"csrf_token": self.csrf()})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.rows(), [])

    def test_the_phone_upload_needs_no_csrf_token(self):
        self.login()                                         # even from a logged-in client
        self.assertEqual(self.post_day().status_code, 200)

    def test_security_headers_are_set(self):
        self.login()
        r = self.client.get("/")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    # -- pages --------------------------------------------------------------
    def test_pages_render_in_both_languages(self):
        self.post_day(speedLog=day_csv({600: 62}), gaitCycles="12", meanSpeed="62",
                      stancePct="58", strideMs="1400", strideCv="30", strideCm="110",
                      excursion01="610")
        self.post_day(device=MAC + "_Battery", steps="94")
        self.login()
        for lang in ("en", "es"):
            for path in ("/", f"/patient/{MAC}", f"/patient/{MAC}/config", "/settings"):
                r = self.client.get(path, query_string={"lang": lang})
                self.assertEqual(r.status_code, 200, f"{path} [{lang}]")
        page = self.client.get(f"/patient/{MAC}").get_data(as_text=True)
        self.assertIn("chart.js@4.6.1", page)
        self.assertIn('integrity="sha384-', page)
        self.assertIn("csrf_token", page)

    def test_the_patient_page_shows_the_most_recent_days_first(self):
        base = now_ms() - 10 * DAY_MS
        for i in range(5):
            self.post_day(captured=base + i * DAY_MS, steps=str(1000 + i))
        self.login()
        page = self.client.get(f"/patient/{MAC}").get_data(as_text=True)
        self.assertIn("const dailySteps   = [1002, 1003, 1004];", page)   # PATIENT_DAYS=3
        everything = self.client.get(f"/patient/{MAC}?days=all").get_data(as_text=True)
        self.assertIn("const dailySteps   = [1000, 1001, 1002, 1003, 1004];", everything)

    def test_health_check_needs_no_login_and_no_database(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)


if __name__ == "__main__":
    unittest.main()
