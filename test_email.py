"""
Send one test email using the SMTP settings configured in the dashboard
(Settings page → stored in the database). Equivalent to the "Send test email"
button, but from the command line.

    python test_email.py
"""
import sqlite3
import app

db = sqlite3.connect(app.DB_PATH)
db.row_factory = sqlite3.Row
cfg = app.get_settings(db)

ok, msg = app.send_email(
    "[StepCounter] Test email",
    "This is a test from the StepCounter server. If you can read this, "
    "patient-failure alerts will reach this inbox.",
    cfg,
)
print(("OK: " if ok else "FAILED: ") + msg)
