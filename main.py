"""
Main orchestrator: scrape → deduplicate → commute filter → email.

Run directly:   python main.py
Via scheduler:  python scheduler.py
"""
import logging
import re
import sys
from typing import List

from config import (
    EMAIL_CONFIG,
    GOOGLE_MAPS_API_KEY,
    LOCATIONS,
    SEARCH_CRITERIA,
)
from commute import check_commutes
from emailer import send_email
from scrapers import Listing, scrape_compass, scrape_redfin, scrape_zillow
from state import filter_new_listings, mark_seen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Deduplication ─────────────────────────────────────────────────────────────

def _normalize_address(address: str) -> str:
    """Lower-case, collapse whitespace, strip punctuation for fuzzy matching."""
    s = address.lower()
    s = re.sub(r"[,#\.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def deduplicate(listings: List[Listing]) -> List[Listing]:
    """
    Merge listings that share the same normalised address.
    Redfin URL wins if available; other sources fill in gaps.
    """
    seen_addr: dict = {}

    for listing in listings:
        key = _normalize_address(listing.address)
        if key not in seen_addr:
            seen_addr[key] = listing
        else:
            existing = seen_addr[key]
            # Merge URLs / photos from other sources
            if listing.redfin_url and not existing.redfin_url:
                existing.redfin_url = listing.redfin_url
            if listing.zillow_url and not existing.zillow_url:
                existing.zillow_url = listing.zillow_url
            if listing.compass_url and not existing.compass_url:
                existing.compass_url = listing.compass_url
            if listing.image_url and not existing.image_url:
                existing.image_url = listing.image_url
            if listing.has_office:
                existing.has_office = True
            # Prefer the Redfin ID for deduplication stability
            if listing.source == "redfin":
                existing.id = listing.id

    deduped = list(seen_addr.values())
    logger.info("After dedup: %d unique listings", len(deduped))
    return deduped


# ── Main run ──────────────────────────────────────────────────────────────────

def run(send_even_if_no_new: bool = False) -> None:
    criteria = SEARCH_CRITERIA
    all_listings: List[Listing] = []

    for loc in LOCATIONS:
        name        = loc["name"]
        fallback_id = loc["redfin_fallback_id"]
        fallback_rt = loc["redfin_region_type"]

        # Redfin (primary)
        rf = scrape_redfin(
            location=name,
            fallback_id=fallback_id,
            fallback_region_type=fallback_rt,
            min_price=criteria["min_price"],
            max_price=criteria["max_price"],
            min_beds=criteria["min_beds"],
            min_baths=criteria["min_baths"],
            min_sqft=criteria["min_sqft"],
        )
        all_listings.extend(rf)

        # Zillow (supplementary)
        zl = scrape_zillow(
            location=name,
            min_price=criteria["min_price"],
            max_price=criteria["max_price"],
            min_beds=criteria["min_beds"],
            min_baths=criteria["min_baths"],
            min_sqft=criteria["min_sqft"],
        )
        all_listings.extend(zl)

        # Compass (supplementary)
        cp = scrape_compass(
            location=name,
            min_price=criteria["min_price"],
            max_price=criteria["max_price"],
            min_beds=criteria["min_beds"],
            min_baths=criteria["min_baths"],
            min_sqft=criteria["min_sqft"],
        )
        all_listings.extend(cp)

    if not all_listings:
        logger.warning("No listings found across all sources and locations.")

    # Deduplicate by address across sources
    unique_listings = deduplicate(all_listings)

    # Filter out listings seen in previous runs
    new_listings = filter_new_listings(unique_listings)

    if not new_listings and not send_even_if_no_new:
        logger.info("No new listings — skipping email.")
        return

    # Commute time check and filter
    passing = check_commutes(
        new_listings,
        api_key=GOOGLE_MAPS_API_KEY,
        max_minutes=criteria["max_commute_minutes"],
    )

    logger.info(
        "%d listings pass commute filter (≤%d min)",
        len(passing),
        criteria["max_commute_minutes"],
    )

    if not passing and not send_even_if_no_new:
        logger.info("All new listings filtered by commute — skipping email.")
        mark_seen(new_listings)  # still mark them so we don't retry
        return

    # Send email
    ok = send_email(passing, EMAIL_CONFIG)

    if ok:
        mark_seen(passing)
        logger.info("Run complete. Email sent with %d listings.", len(passing))
    else:
        logger.error("Email send failed — listings NOT marked as seen (will retry next run).")


if __name__ == "__main__":
    force = "--force" in sys.argv
    run(send_even_if_no_new=force)
