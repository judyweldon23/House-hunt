"""
Google Maps Distance Matrix API — commute time checker.

Checks driving duration at 8:00 AM on the next Wednesday from each listing
address to 100 Clarendon St, Boston, MA 02116.
"""
import datetime
import logging
import time
from typing import Optional

import pytz
import requests

logger = logging.getLogger(__name__)

DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"
DESTINATION = "100 Clarendon St, Boston, MA 02116"
EASTERN = pytz.timezone("America/New_York")


def _next_wednesday_8am_unix() -> int:
    """Return the Unix timestamp for the next Wednesday at 08:00 AM Eastern."""
    now = datetime.datetime.now(EASTERN)
    days_ahead = (2 - now.weekday()) % 7  # Wednesday = weekday 2
    if days_ahead == 0:
        days_ahead = 7  # If today is Wednesday, use next week
    next_wed = now + datetime.timedelta(days=days_ahead)
    departure = next_wed.replace(hour=8, minute=0, second=0, microsecond=0)
    return int(departure.timestamp())


def get_commute_minutes(address: str, api_key: str) -> Optional[int]:
    """
    Return driving time in minutes from `address` to the destination
    at 8:00 AM next Wednesday.  Returns None on error.
    """
    if not api_key:
        logger.warning("GOOGLE_MAPS_API_KEY not set — skipping commute check")
        return None

    departure_time = _next_wednesday_8am_unix()
    params = {
        "origins":          address,
        "destinations":     DESTINATION,
        "departure_time":   departure_time,
        "traffic_model":    "best_guess",
        "mode":             "driving",
        "key":              api_key,
    }

    try:
        resp = requests.get(DISTANCE_MATRIX_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status", "")
        if status != "OK":
            logger.warning("Distance Matrix API returned status '%s' for %s", status, address)
            return None

        element = data["rows"][0]["elements"][0]
        if element.get("status") != "OK":
            logger.warning("No route found for %s: %s", address, element.get("status"))
            return None

        # Prefer duration_in_traffic (uses live/historical traffic)
        duration_sec = (
            element.get("duration_in_traffic", {}).get("value")
            or element.get("duration", {}).get("value")
        )
        if duration_sec is None:
            return None

        return int(duration_sec / 60)

    except Exception as exc:
        logger.error("Commute check failed for '%s': %s", address, exc)
        return None


def check_commutes(listings, api_key: str, max_minutes: int) -> list:
    """
    For each listing, check commute time and filter out those exceeding
    max_minutes.  If api_key is missing, keep all listings but mark
    commute_minutes as None.
    """
    if not api_key:
        logger.warning(
            "No Google Maps API key — including all listings without commute filter"
        )
        return listings

    passing = []
    for listing in listings:
        minutes = get_commute_minutes(listing.address, api_key)
        listing.commute_minutes = minutes
        if minutes is None:
            # API error — include listing with a note
            passing.append(listing)
        elif minutes <= max_minutes:
            passing.append(listing)
        else:
            logger.info(
                "Filtered out %s (%d min commute > %d min limit)",
                listing.address,
                minutes,
                max_minutes,
            )
        time.sleep(0.15)  # stay within free-tier rate limits

    return passing
