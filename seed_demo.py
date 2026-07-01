"""
Populate the database with three illustrative patients so the dashboard and
charts can be screenshotted without a physical device.

    python seed_demo.py

Creates:
    AA_BB_CC_DD_EE_01  -> OK     (healthy daily activity, reporting now)
    AA_BB_CC_DD_EE_02  -> LOW    (very few steps on the latest day)
    AA_BB_CC_DD_EE_03  -> SILENT (last report is days old)
"""
import os
import time
import random
import sqlite3

DB_PATH = os.environ.get("DB_PATH", "stepcounter.db")
DAY_MS = 86_400_000
MIN_PER_DAY = 1440


def day_log(target_steps):
    """Build a 1440-value per-minute CSV that sums to ~target_steps,
    concentrated in plausible waking-hour bursts."""
    log = [0] * MIN_PER_DAY
    remaining = target_steps
    # Activity windows (minute-of-day ranges): morning, midday, evening.
    windows = [(420, 540), (780, 900), (1080, 1260)]
    while remaining > 0:
        lo, hi = random.choice(windows)
        m = random.randint(lo, hi)
        burst = min(remaining, random.randint(20, 110))  # steps/min within human cadence
        log[m] = min(255, log[m] + burst)
        remaining -= burst
    return ",".join(str(v) for v in log), sum(log)


def insert_patient(db, device_id, daily_targets, last_offset_days=0):
    now = int(time.time() * 1000)
    n = len(daily_targets)
    for i, target in enumerate(daily_targets):
        # captured_at walks from oldest to newest; last_offset_days ages the newest.
        captured = now - (last_offset_days + (n - 1 - i)) * DAY_MS
        csv_log, total = day_log(target)
        db.execute(
            "INSERT INTO readings (device_id, captured_at, total_steps, minute_log, received_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (device_id, captured, total, csv_log, captured),
        )


def main():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL, captured_at INTEGER NOT NULL,
            total_steps INTEGER NOT NULL, minute_log TEXT NOT NULL,
            received_at INTEGER NOT NULL);
        """
    )
    db.execute("DELETE FROM readings WHERE device_id LIKE 'AA_BB_CC_DD_EE_%'")

    insert_patient(db, "AA_BB_CC_DD_EE_01", [3200, 4100, 3800, 4500, 3900, 4200, 4000])
    insert_patient(db, "AA_BB_CC_DD_EE_02", [3000, 2800, 2500, 1900, 1200, 700, 300])
    insert_patient(db, "AA_BB_CC_DD_EE_03", [3500, 3300, 3600], last_offset_days=4)

    db.commit()
    db.close()
    print(f"Seeded demo patients into {DB_PATH}")


if __name__ == "__main__":
    main()
