"""
Send one test email using the SMTP settings configured in the dashboard
(Settings page → stored in the database). Equivalent to the "Send test email"
button, but from the command line. Uses whichever database the app is
configured for (DATABASE_URL, else the local SQLite file).

    python test_email.py
"""
import os

os.environ.setdefault("RUN_ALERTER", "0")     # this is a one-shot, not the server

import app  # noqa: E402 - must follow the environment setup above
from db import connect  # noqa: E402

db = connect()
try:
    cfg = app.get_settings(db)
finally:
    db.close()

ok, msg = app.send_email(
    "[StepCounter] Test email",
    "This is a test from the StepCounter server. If you can read this, "
    "patient-failure alerts will reach this inbox.",
    cfg,
)
print(("OK: " if ok else "FAILED: ") + msg)
