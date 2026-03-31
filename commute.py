"""
Commute-time checker — no API key required by default.

Primary (if GOOGLE_MAPS_API_KEY is set):
  Google Maps Distance Matrix API with real historical traffic data.

Automatic fallback — zero cost, no signup, no key:
  • Nominatim (OpenStreetMap) for geocoding
  • Haversine straight-line distance → driving-time estimate
    (road factor 1.4× + Boston 8 am rush-hour factor 1.35×)

Both paths check the driving time at 8:00 AM on the next Wednesday.
"""
import datetime
import logging
import math
import re
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

# Driving estimate factors applied to straight-line Haversine distance:
#   ROAD_FACTOR    — actual road distance ≈ 1.4× straight-line in metro Boston
#   AVG_SPEED_MPH  — average urban/suburban speed before rush hour
#   RUSH_HOUR_MULT — Boston 8 am traffic adds ~35% to free-flow time
ROAD_FACTOR       = 1.4
AVG_SPEED_MPH     = 28.0
RUSH_HOUR_MULTIPLIER = 1.35

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GMAPS_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

NOMINATIM_HEADERS = {
    "User-Agent": "HouseHuntScraper/1.0 (judy.weldon@Baincapital.com)"
}

# Zip-code center coordinates used as a fallback when Nominatim fails.
# Accurate to within ~1 mile for these Boston suburbs.
_ZIP_COORDS: dict = {
    "02446": (42.330, -71.115),   # Brookline (Coolidge Corner / Washington Sq)
    "02445": (42.328, -71.135),   # Brookline (south)
    "02467": (42.320, -71.163),   # Chestnut Hill
    "02468": (42.325, -71.222),   # Waban
    "02461": (42.320, -71.207),   # Newton Highlands
    "02465": (42.349, -71.232),   # West Newton
    "02481": (42.302, -71.293),   # Wellesley Hills
    "02482": (42.296, -71.276),   # Wellesley center
    "02459": (42.328, -71.192),   # Newton Centre
    "02460": (42.347, -71.209),   # Newtonville
    "02462": (42.347, -71.257),   # Newton Lower Falls
    "02464": (42.338, -71.244),   # Newton Upper Falls
    "02466": (42.348, -71.247),   # Auburndale
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


# ── Haversine routing estimate ────────────────────────────────────────────────

def _haversine_drive_minutes(from_lat: float, from_lon: float) -> float:
    """Estimate rush-hour driving time using Haversine straight-line distance."""
    R = 3956.0  # Earth radius in miles
    lat1 = math.radians(from_lat); lon1 = math.radians(from_lon)
    lat2 = math.radians(DEST_LAT);  lon2 = math.radians(DEST_LON)
    dlat = lat2 - lat1; dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    straight_miles = 2 * R * math.asin(math.sqrt(a))
    drive_miles = straight_miles * ROAD_FACTOR
    base_minutes = (drive_miles / AVG_SPEED_MPH) * 60
    return base_minutes * RUSH_HOUR_MULTIPLIER


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

    Uses Google Maps if api_key is set, otherwise Nominatim + Haversine.
    is_estimate=True signals the Haversine path (rush-hour multiplier applied).
    """
    if api_key:
        mins = _gmaps_drive_minutes(address, api_key)
        return mins, False

    # Haversine path — try Nominatim first, fall back to zip-code center
    coords = _geocode(address)
    if coords is None:
        zip_match = re.search(r"\b(\d{5})\b", address)
        if zip_match:
            coords = _ZIP_COORDS.get(zip_match.group(1))
        if coords is None:
            return None, True
    else:
        time.sleep(1.1)  # Nominatim requests 1 req/sec
    rush_mins = int(_haversine_drive_minutes(coords[0], coords[1]))
    return rush_mins, True


def check_commutes(listings, api_key: str, max_minutes: int) -> list:
    """
    Annotate each listing with commute_minutes / commute_is_estimate,
    then filter out those exceeding max_minutes.
    Listings whose commute can't be determined are kept with a note.
    """
    method = "Google Maps" if api_key else "Haversine (free, rush-hour estimate)"
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
