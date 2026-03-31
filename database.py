"""
SQLite persistence layer for the mobile app.

All listings that pass criteria are stored here so the PWA can display
them even between scraper runs.
"""
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import List

from scrapers.base import Listing

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "listings.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id                  TEXT PRIMARY KEY,
    address             TEXT NOT NULL,
    price               INTEGER NOT NULL,
    beds                INTEGER NOT NULL,
    baths               REAL NOT NULL,
    sqft                INTEGER NOT NULL,
    image_url           TEXT,
    redfin_url          TEXT,
    zillow_url          TEXT,
    compass_url         TEXT,
    commute_minutes     INTEGER,
    commute_is_estimate INTEGER DEFAULT 0,
    has_office          INTEGER DEFAULT 0,
    priority_score      INTEGER DEFAULT 0,
    source              TEXT DEFAULT '',
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


def upsert_listings(listings: List[Listing]) -> None:
    """Insert new listings or update URLs/commute for existing ones."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        for l in listings:
            existing = conn.execute(
                "SELECT id, first_seen FROM listings WHERE id = ?", (l.id,)
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE listings SET
                        price=?, beds=?, baths=?, sqft=?,
                        image_url=COALESCE(?, image_url),
                        redfin_url=COALESCE(?, redfin_url),
                        zillow_url=COALESCE(?, zillow_url),
                        compass_url=COALESCE(?, compass_url),
                        commute_minutes=COALESCE(?, commute_minutes),
                        commute_is_estimate=?,
                        has_office=MAX(has_office, ?),
                        priority_score=?,
                        last_seen=?
                    WHERE id=?""",
                    (
                        l.price, l.beds, l.baths, l.sqft,
                        l.image_url, l.redfin_url, l.zillow_url, l.compass_url,
                        l.commute_minutes, int(l.commute_is_estimate),
                        int(l.has_office), l.priority_score,
                        now, l.id,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO listings
                        (id, address, price, beds, baths, sqft,
                         image_url, redfin_url, zillow_url, compass_url,
                         commute_minutes, commute_is_estimate, has_office,
                         priority_score, source, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        l.id, l.address, l.price, l.beds, l.baths, l.sqft,
                        l.image_url, l.redfin_url, l.zillow_url, l.compass_url,
                        l.commute_minutes, int(l.commute_is_estimate),
                        int(l.has_office), l.priority_score,
                        l.source, now, now,
                    ),
                )
    logger.info("Upserted %d listings into database", len(listings))


def get_all_listings() -> List[dict]:
    """Return all listings sorted by priority then price, as dicts."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM listings
               ORDER BY priority_score DESC, price ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        last  = conn.execute("SELECT MAX(last_seen) FROM listings").fetchone()[0]
    return {"total_listings": count, "last_updated": last}
