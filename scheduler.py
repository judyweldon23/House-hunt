"""
Persistent scheduler — runs `main.run()` every 2 days.

Usage:
    python scheduler.py

The process must stay running (e.g. via tmux, screen, or a systemd service).
On first start it runs immediately, then every 48 hours.
"""
import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import SCRAPE_INTERVAL_DAYS
import main as house_hunt_main

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def job():
    logger.info("=== Scheduled house-hunt run starting ===")
    try:
        house_hunt_main.run()
    except Exception as exc:
        logger.exception("Unhandled error during run: %s", exc)
    logger.info("=== Scheduled house-hunt run complete ===")


if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone="America/New_York")

    # Run once immediately at startup
    scheduler.add_job(job, trigger="date", id="first_run")

    # Then every N days
    scheduler.add_job(
        job,
        trigger=IntervalTrigger(days=SCRAPE_INTERVAL_DAYS),
        id="recurring_run",
        max_instances=1,
        coalesce=True,
    )

    logger.info(
        "Scheduler started — running every %d day(s). Press Ctrl+C to stop.",
        SCRAPE_INTERVAL_DAYS,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
        sys.exit(0)
