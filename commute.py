"""
Commute-time checker — no API key required by default.

Primary (if GOOGLE_MAPS_API_KEY is set):
  Google Maps Distance Matrix API with real historical traffic data.

Automatic fallback — zero cost, no signup, no key:
  • Nominatim (OpenStreetMap) for geocoding
  • OSRM public server for driving-time routing
  • 1.35× rush-hour multiplier to model Boston 8 am traffic

Both paths check the driving time at 8:00 AM on the next Wednesday.
"""
import datetime
import logging
import time
from typing import Optional

import pytz
import requests

logger = logging.getLogger(__name__)

EASTERN = pytz.timezone("America/New_York")
DESTINATION = "100 Clarendon St, Boston, MA 02116"

# Pre-geocoded destination coordinates (saves one Nominatim call per run)
DEST_LAT = 42.34937
DEST_LON = -71.07438

# Rush-hour traffic multiplier for the OSRM free-flow estimate.
# OSRM gives drive time with no traffic; Boston 8 am adds ~35% on these routes.
RUSH_HOUR_MULTIPLIER = 1.35

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
GMAPS_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

NOMINATIM_HEADERS = {
    "User-Agent": "HouseHuntScraper/1.0 (judy.weldon@Baincapital.com)"
}

# Simple in-process geocode cache to avoid repeat Nominatim lookups
_geocode_cache: dict = {}


def _next_wednesday_8am_unix() -> int:
    """Unix timestamp for next Wednesday at 08:00 AM Eastern."""
    now = datetime.datetime.now(EASTERN)
    days_ahead = (2 - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    next_wed = now + datetime.timedelta(days=days_ahead)
    departure = next_wed.replace(hour=8, minute=0, second=0, microsecond=0)
    return int(departure.timestamp())


# ── Geocoding ─────────────────────────────────────────────────────────────────

def _geocode(address: str) -> Optional[tuple]:
    """Return (lat, lon) for an address via Nominatim, or None on failure."""
    if address in _geocode_cache:
        return _geocode_cache[address]

    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers=NOMINATIM_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            logger.warning("Nominatim: no results for '%s'", address)
            return None
        lat = float(results[0]["lat"])
        lon = float(results[0]["lon"])
        _geocode_cache[address] = (lat, lon)
        return lat, lon
    except Exception as exc:
        logger.warning("Nominatim geocode failed for '%s': %s", address, exc)
        return None


# ── OSRM routing ──────────────────────────────────────────────────────────────

def _osrm_drive_minutes(from_lat: float, from_lon: float) -> Optional[float]:
    """
    Free-flow driving minutes via the OSRM public API.
    Multiply by RUSH_HOUR_MULTIPLIER before comparing to limit.
    """
    url = OSRM_URL.format(
        lon1=from_lon, lat1=from_lat,
        lon2=DEST_LON,  lat2=DEST_LAT,
    )
    try:
        resp = requests.get(url, params={"overview": "false"}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            logger.warning("OSRM returned no route: %s", data.get("message", ""))
            return None
        return data["routes"][0]["duration"] / 60  # seconds → minutes
    except Exception as exc:
        logger.warning("OSRM routing failed: %s", exc)
        return None


# ── Google Maps (optional) ────────────────────────────────────────────────────

def _gmaps_drive_minutes(address: str, api_key: str) -> Optional[int]:
    """Real traffic-aware commute via Google Maps Distance Matrix API."""
    params = {
        "origins":        address,
        "destinations":   DESTINATION,
        "departure_time": _next_wednesday_8am_unix(),
        "traffic_model":  "best_guess",
        "mode":           "driving",
        "key":            api_key,
    }
    try:
        resp = requests.get(GMAPS_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "OK":
            logger.warning("Google Maps status '%s' for %s", data.get("status"), address)
            return None
        element = data["rows"][0]["elements"][0]
        if element.get("status") != "OK":
            return None
        sec = (
            element.get("duration_in_traffic", {}).get("value")
            or element.get("duration", {}).get("value")
        )
        return int(sec / 60) if sec else None
    except Exception as exc:
        logger.error("Google Maps call failed for '%s': %s", address, exc)
        return None


# ── Public interface ──────────────────────────────────────────────────────────

def get_commute_minutes(address: str, api_key: str = "") -> tuple:
    """
    Return (minutes: int|None, is_estimate: bool).

    Uses Google Maps if api_key is set, otherwise OSRM + Nominatim.
    is_estimate=True signals the OSRM path (rush-hour multiplier applied).
    """
    if api_key:
        mins = _gmaps_drive_minutes(address, api_key)
        return mins, False

    # OSRM path
    coords = _geocode(address)
    if coords is None:
        return None, True
    time.sleep(1.1)  # Nominatim requests 1 req/sec
    free_flow = _osrm_drive_minutes(coords[0], coords[1])
    if free_flow is None:
        return None, True
    rush_mins = int(free_flow * RUSH_HOUR_MULTIPLIER)
    return rush_mins, True


def check_commutes(listings, api_key: str, max_minutes: int) -> list:
    """
    Annotate each listing with commute_minutes / commute_is_estimate,
    then filter out those exceeding max_minutes.
    Listings whose commute can't be determined are kept with a note.
    """
    method = "Google Maps" if api_key else "OSRM (free, rush-hour estimate)"
    logger.info("Checking commutes via %s (limit: %d min)", method, max_minutes)

    passing = []
    for listing in listings:
        mins, is_est = get_commute_minutes(listing.address, api_key)
        listing.commute_minutes = mins
        listing.commute_is_estimate = is_est

        if mins is None:
            logger.info("Could not determine commute for %s — keeping", listing.address)
            passing.append(listing)
        elif mins <= max_minutes:
            passing.append(listing)
        else:
            logger.info(
                "Filtered: %s  (%d min%s > %d min limit)",
                listing.address, mins,
                " est." if is_est else "",
                max_minutes,
            )
        time.sleep(0.5)  # polite pacing for public APIs

    return passing
