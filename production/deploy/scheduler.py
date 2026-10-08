#!/usr/bin/env python
"""Runs the project's periodic management commands (stdlib only, no cron needed).

Schedule comes from AGENTS.md ("Nightly jobs"), in Asia/Manila time:
  every 15 min  send_event_reminders
  hourly (:05)  fetch_jil_videos
  nightly       the rest, one after another, starting at SCHEDULER_DAILY_HOUR (default 02:00)

A command that does not exist yet is skipped and logged, so this is safe to run
before every job has been built. Jobs run one at a time; if a long job overlaps
a tick, that tick is skipped.
"""
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("SCHEDULER_TZ", "Asia/Manila"))
DAILY_HOUR = int(os.environ.get("SCHEDULER_DAILY_HOUR", "2"))
JOB_TIMEOUT = 3600  # seconds

EVERY_15_MIN = ["send_event_reminders"]
HOURLY = ["fetch_jil_videos"]
# Order matters: archive first, purge last.
NIGHTLY = [
    "archive_inactive_users",
    "auto_transfer_extension",
    "complete_ended_goals",
    "cleanup_email_tokens",
    "cleanup_notifications",
    "cleanup_audit_log",
    "purge_archived",
]


def log(msg):
    print(f"[scheduler {datetime.now(TZ):%Y-%m-%d %H:%M}] {msg}", flush=True)


def due(now):
    """Return the command names that should run at this minute."""
    jobs = []
    if now.minute % 15 == 0:
        jobs += EVERY_15_MIN
    if now.minute == 5:
        jobs += HOURLY
    if now.hour == DAILY_HOUR and now.minute == 0:
        jobs += NIGHTLY
    return jobs


def available_commands():
    """Names of the manage.py commands that exist right now."""
    out = subprocess.run(
        [sys.executable, "manage.py", "help", "--commands"],
        capture_output=True, text=True,
    )
    return set(out.stdout.split())


def run_jobs(names):
    have = available_commands()
    for name in names:
        if name not in have:
            log(f"skip {name}: command not found (not built yet?)")
            continue
        log(f"start {name}")
        try:
            result = subprocess.run(
                [sys.executable, "manage.py", name], timeout=JOB_TIMEOUT
            )
            log(f"done {name} (exit {result.returncode})")
        except subprocess.TimeoutExpired:
            log(f"TIMEOUT {name} after {JOB_TIMEOUT}s")


def main():
    # PID 1 ignores SIGTERM by default; exit cleanly so `docker stop` is fast.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log(f"started; timezone {TZ}, nightly jobs at {DAILY_HOUR:02d}:00")
    last = None
    while True:
        now = datetime.now(TZ).replace(second=0, microsecond=0)
        if now != last:
            last = now
            jobs = due(now)
            if jobs:
                run_jobs(jobs)
        time.sleep(10)


if __name__ == "__main__":
    main()