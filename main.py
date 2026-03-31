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
    Replace docs/listings.json with the full current set of matching listings.
    Called with ALL passing listings every run so the web page always reflects
    what is live on the market right now (sold/removed listings disappear automatically).
    """
    now = datetime.now(timezone.utc).isoformat()
    sorted_data = sorted(
        [_listing_to_dict(l, now) for l in listings],
        key=lambda x: (-x.get("priority_score", 0), x.get("price", 0)),
    )
    os.makedirs(os.path.dirname(WEB_JSON), exist_ok=True)
    with open(WEB_JSON, "w") as f:
        json.dump(
            {"listings": sorted_data, "last_updated": now, "total": len(sorted_data)},
            f, indent=2,
        )
    logger.info("Wrote %d listings to docs/listings.json", len(sorted_data))


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

    # ── Commute filter — run against ALL listings every time ──────────────────
    # The web page must always show the complete current picture, so we check
    # commutes for every listing on every run (OSRM is free, ~0.5s each).
    all_passing = check_commutes(
        unique_listings,
        api_key=GOOGLE_MAPS_API_KEY,
        max_minutes=criteria["max_commute_minutes"],
    )
    logger.info("%d / %d listings pass commute filter", len(all_passing), len(unique_listings))

    # ── Always persist the full current set ───────────────────────────────────
    export_web_json(all_passing)      # GitHub Pages — replaces previous snapshot
    database.upsert_listings(all_passing)

    # ── Email: first run = everything; subsequent runs = new listings only ─────
    first = is_first_run()
    to_email = all_passing if first else filter_new_listings(all_passing)

    if not to_email:
        logger.info("No new listings — skipping email.")
        return

    ok = send_email(to_email, EMAIL_CONFIG)
    if ok:
        mark_seen(to_email)
        logger.info("Email sent with %d listing(s).", len(to_email))
    else:
        logger.error("Email failed — listings NOT marked seen (will retry next run).")


if __name__ == "__main__":
    import traceback
    try:
        run()
    except Exception:
        traceback.print_exc()
        raise   # re-raise so exit code 1 signals the workflow step failed
