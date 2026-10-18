"""
Populate the database with three illustrative patients so the dashboard and
charts can be screenshotted without a physical device.

    python seed_demo.py

Creates:
    AA_BB_CC_DD_EE_01  -> OK     (healthy daily activity, reporting now)
    AA_BB_CC_DD_EE_02  -> LOW    (very few steps on the latest day)
    AA_BB_CC_DD_EE_03  -> SILENT (last report is days old)

Patient 01 and 02 also carry gait/speed data, 02 with the short stance and high
stride variability of an asymmetric prosthetic gait. Patient 03 carries none, so
the "no gait data" path stays visible on the charts.
"""
import time
import random

from db import connect, init_schema

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


# Walking speed travels as one byte per minute; this is the m/s each unit is
# worth. Keep in step with GAIT_SPEED_LSB in app.py and Config.h.
SPEED_LSB = 0.02


def gait_day(minute_log, speed_ms, stance_pct, cadence):
    """A plausible gait block for a day, sampled the way the device samples it:
    a speed reading only in the minutes it actually ran a gyro burst, which is
    at most one minute in five of walking."""
    minutes = [int(x) for x in minute_log.split(",")]
    speeds = [0] * MIN_PER_DAY
    cycles = 0
    for i, steps in enumerate(minutes):
        if steps >= 20 and i % 5 == 0:                 # the device's own burst gating
            jitter = random.uniform(-0.12, 0.12)
            speeds[i] = max(1, min(255, round((speed_ms + jitter) / SPEED_LSB)))
            cycles += random.randint(3, 6)
    if cycles == 0:
        return None
    measured = [v for v in speeds if v]
    stride_ms = round(120000 / cadence)                # one stride = two steps
    return {
        "speed_log": ",".join(str(v) for v in speeds),
        "cycles": cycles,
        "mean_speed": round(sum(measured) / len(measured)),
        "stance_pct": stance_pct,
        "stride_ms": stride_ms,
        # A steady gait varies by a few percent; an asymmetric one by much more.
        "stride_cv": random.randint(15, 35) if stance_pct >= 58 else random.randint(60, 110),
        "stride_cm": round(speed_ms * stride_ms / 10),  # m/s * s * 100 cm
        "excursion_01": random.randint(550, 700),
    }


def insert_patient(db, device_id, daily_targets, last_offset_days=0,
                   speed_ms=None, stance_pct=60, cadence=110):
    now = int(time.time() * 1000)
    n = len(daily_targets)
    for i, target in enumerate(daily_targets):
        # captured_at walks from oldest to newest; last_offset_days ages the newest.
        captured = now - (last_offset_days + (n - 1 - i)) * DAY_MS
        csv_log, total = day_log(target)
        g = gait_day(csv_log, speed_ms, stance_pct, cadence) if speed_ms else None
        db.execute(
            "INSERT INTO readings (device_id, captured_at, total_steps, minute_log, received_at,"
            " speed_log, gait_cycles, mean_speed, stance_pct, stride_ms, stride_cv,"
            " stride_cm, excursion_01)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (device_id, captured, total, csv_log, captured,
             g and g["speed_log"], g and g["cycles"], g and g["mean_speed"],
             g and g["stance_pct"], g and g["stride_ms"], g and g["stride_cv"],
             g and g["stride_cm"], g and g["excursion_01"]),
        )


def main():
    init_schema()                       # same schema as the app, on either engine
    db = connect()

    # Wildcard goes in the parameter, not the SQL, so the '%' is never mistaken
    # for a psycopg placeholder.
    db.execute("DELETE FROM readings WHERE device_id LIKE ?", ("AA_BB_CC_DD_EE_%",))

    insert_patient(db, "AA_BB_CC_DD_EE_01", [3200, 4100, 3800, 4500, 3900, 4200, 4000],
                   speed_ms=1.25, stance_pct=60, cadence=110)
    # Slow, short-stance, highly variable: what an asymmetric prosthetic gait
    # looks like next to the healthy one above.
    insert_patient(db, "AA_BB_CC_DD_EE_02", [3000, 2800, 2500, 1900, 1200, 700, 300],
                   speed_ms=0.62, stance_pct=53, cadence=85)
    # No gait block at all -- step-only firmware.
    insert_patient(db, "AA_BB_CC_DD_EE_03", [3500, 3300, 3600], last_offset_days=4)

    db.commit()
    db.close()
    print("Seeded demo patients")


if __name__ == "__main__":
    main()
