"""
Main orchestrator: scrape → deduplicate → commute filter → email + persist.

Run directly:   python main.py
Via scheduler:  python scheduler.py  (local / VPS)
Via CI:         GitHub Actions runs this on a schedule (see .github/workflows/)

First run:  emails ALL current matching listings.
Subsequent: emails only NEW listings.

Listings are always written to:
  • data/listings.db      (SQLite, for the local Flask app)
  • web/listings.json     (JSON,   for the GitHub Pages website)
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import List

from config import (
    EMAIL_CONFIG,
    GOOGLE_MAPS_API_KEY,
    LOCATIONS,
    SEARCH_CRITERIA,
)
from commute import check_commutes
import database
from emailer import send_email
from scrapers import Listing, scrape_compass, scrape_redfin, scrape_zillow
from state import filter_new_listings, is_first_run, mark_seen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

WEB_JSON = os.path.join(os.path.dirname(__file__), "docs", "listings.json")


# ── Deduplication ─────────────────────────────────────────────────────────────

def _normalize_address(address: str) -> str:
    s = address.lower()
    s = re.sub(r"[,#\.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def deduplicate(listings: List[Listing]) -> List[Listing]:
    seen_addr: dict = {}
    for listing in listings:
        key = _normalize_address(listing.address)
        if key not in seen_addr:
            seen_addr[key] = listing
        else:
            existing = seen_addr[key]
            if listing.redfin_url  and not existing.redfin_url:  existing.redfin_url  = listing.redfin_url
            if listing.zillow_url  and not existing.zillow_url:  existing.zillow_url  = listing.zillow_url
            if listing.compass_url and not existing.compass_url: existing.compass_url = listing.compass_url
            if listing.image_url   and not existing.image_url:   existing.image_url   = listing.image_url
            if listing.has_office:
                existing.has_office = True
            if listing.source == "redfin":
                existing.id = listing.id
    deduped = list(seen_addr.values())
    logger.info("After dedup: %d unique listings", len(deduped))
    return deduped


# ── Web JSON export (GitHub Pages) ────────────────────────────────────────────

def _listing_to_dict(l: Listing, now: str) -> dict:
    return {
        "id":                  l.id,
        "address":             l.address,
        "price":               l.price,
        "beds":                l.beds,
        "baths":               l.baths,
        "sqft":                l.sqft,
        "image_url":           l.image_url,
        "redfin_url":          l.redfin_url,
        "zillow_url":          l.zillow_url,
        "compass_url":         l.compass_url,
        "commute_minutes":     l.commute_minutes,
        "commute_is_estimate": l.commute_is_estimate,
        "has_office":          l.has_office,
        "priority_score":      l.priority_score,
        "source":              l.source,
        "last_seen":           now,
    }


def export_web_json(listings: List[Listing]) -> None:
    """
    Merge `listings` into web/listings.json, accumulating across runs.
    The JSON file is the data source for the GitHub Pages website.
    """
    try:
        with open(WEB_JSON) as f:
            existing = {d["id"]: d for d in json.load(f).get("listings", [])}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        existing = {}

    now = datetime.now(timezone.utc).isoformat()
    for l in listings:
        existing[l.id] = _listing_to_dict(l, now)

    sorted_data = sorted(
        existing.values(),
        key=lambda x: (-x.get("priority_score", 0), x.get("price", 0)),
    )

    os.makedirs(os.path.dirname(WEB_JSON), exist_ok=True)
    with open(WEB_JSON, "w") as f:
        json.dump(
            {"listings": sorted_data, "last_updated": now, "total": len(sorted_data)},
            f, indent=2,
        )
    logger.info("Wrote %d total listings to web/listings.json", len(sorted_data))


# ── Main run ──────────────────────────────────────────────────────────────────

def run() -> None:
    database.init_db()
    criteria = SEARCH_CRITERIA
    all_listings: List[Listing] = []

    for loc in LOCATIONS:
        name        = loc["name"]
        fallback_id = loc["redfin_fallback_id"]
        fallback_rt = loc["redfin_region_type"]

        all_listings.extend(scrape_redfin(
            location=name, fallback_id=fallback_id, fallback_region_type=fallback_rt,
            min_price=criteria["min_price"], max_price=criteria["max_price"],
            min_beds=criteria["min_beds"], min_baths=criteria["min_baths"],
            min_sqft=criteria["min_sqft"],
        ))
        all_listings.extend(scrape_zillow(
            location=name,
            min_price=criteria["min_price"], max_price=criteria["max_price"],
            min_beds=criteria["min_beds"], min_baths=criteria["min_baths"],
            min_sqft=criteria["min_sqft"],
        ))
        all_listings.extend(scrape_compass(
            location=name,
            min_price=criteria["min_price"], max_price=criteria["max_price"],
            min_beds=criteria["min_beds"], min_baths=criteria["min_baths"],
            min_sqft=criteria["min_sqft"],
        ))

    if not all_listings:
        logger.warning("No listings found across all sources and locations.")
        return

    unique_listings = deduplicate(all_listings)

    # ── Decide what to email ──────────────────────────────────────────────────
    first = is_first_run()
    if first:
        logger.info("First run — emailing ALL %d current listings", len(unique_listings))
        to_check = unique_listings
    else:
        to_check = filter_new_listings(unique_listings)
        if not to_check:
            logger.info("No new listings — skipping email.")
            database.upsert_listings(unique_listings)
            export_web_json(unique_listings)
            return

    # ── Commute filter ────────────────────────────────────────────────────────
    passing = check_commutes(
        to_check,
        api_key=GOOGLE_MAPS_API_KEY,
        max_minutes=criteria["max_commute_minutes"],
    )
    logger.info("%d listings pass commute filter", len(passing))

    if not passing:
        logger.info("All listings filtered by commute — skipping email.")
        mark_seen(to_check)
        database.upsert_listings(unique_listings)
        export_web_json(unique_listings)
        return

    # ── Email ─────────────────────────────────────────────────────────────────
    ok = send_email(passing, EMAIL_CONFIG)
    if ok:
        mark_seen(passing)
        logger.info("Email sent with %d listings.", len(passing))
    else:
        logger.error("Email failed — listings NOT marked seen (will retry next run).")

    # ── Persist (always) ──────────────────────────────────────────────────────
    database.upsert_listings(passing)
    export_web_json(passing)


if __name__ == "__main__":
    run()
