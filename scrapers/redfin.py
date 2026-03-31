"""Redfin scraper using Redfin's unofficial GIS/stingray API."""
import json
import logging
import re
import time
from typing import List, Optional

import requests

from .base import Listing

logger = logging.getLogger(__name__)

BASE_URL = "https://www.redfin.com"
GIS_URL = f"{BASE_URL}/stingray/api/gis"
AUTOCOMPLETE_URL = f"{BASE_URL}/stingray/api/search/autocomplete"
DETAILS_URL = f"{BASE_URL}/stingray/api/home/details/belowTheFold"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.redfin.com/",
    "X-Requested-With": "XMLHttpRequest",
}

# region_type codes: 6=city, 2=neighborhood, 5=zip, 4=county
REGION_TYPE_MAP = {
    "City": 6,
    "Neighborhood": 2,
    "Zip Code": 5,
    "County": 4,
    "Metro Area": 3,
}

OFFICE_KEYWORDS = {"office", "study", "home office", "library", "den"}


def _parse_rf_json(text: str) -> dict:
    """Redfin prefixes JSON responses with '{}&&' — strip it."""
    if text.startswith("{}&&"):
        text = text[4:]
    return json.loads(text)


def _lookup_region(location: str, fallback_id: str, fallback_type: int) -> dict:
    """Return {'region_id': str, 'region_type': int} for a location string."""
    try:
        resp = requests.get(
            AUTOCOMPLETE_URL,
            params={"location": location, "v": "2"},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = _parse_rf_json(resp.text)
        for section in data.get("payload", {}).get("sections", []):
            for row in section.get("rows", []):
                rtype = REGION_TYPE_MAP.get(row.get("type", ""), 6)
                return {"region_id": str(row["id"]), "region_type": rtype}
    except Exception as exc:
        logger.warning("Redfin region lookup failed for '%s': %s", location, exc)
    return {"region_id": fallback_id, "region_type": fallback_type}


def _check_has_office(property_id: str) -> bool:
    """Fetch listing details to detect office/study rooms."""
    try:
        resp = requests.get(
            DETAILS_URL,
            params={"propertyId": property_id, "accessLevel": "1"},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = _parse_rf_json(resp.text)
        payload = data.get("payload", {})
        super_groups = (
            payload.get("amenitiesInfo", {}).get("superGroups", [])
        )
        for group in super_groups:
            for sub in group.get("amenityGroups", []):
                for entry in sub.get("amenityEntries", []):
                    name = entry.get("amenityName", "").lower()
                    if any(kw in name for kw in OFFICE_KEYWORDS):
                        return True
    except Exception as exc:
        logger.debug("Office check failed for property %s: %s", property_id, exc)
    return False


def _extract_property_id(url_path: str) -> Optional[str]:
    """Extract numeric property ID from a Redfin URL path like /MA/.../home/12345."""
    match = re.search(r"/home/(\d+)", url_path)
    return match.group(1) if match else None


def scrape_redfin(
    location: str,
    fallback_id: str,
    fallback_region_type: int,
    min_price: int,
    max_price: int,
    min_beds: int,
    min_baths: int,
    min_sqft: int,
) -> List[Listing]:
    """Return Redfin listings matching criteria for one location."""
    listings: List[Listing] = []

    region = _lookup_region(location, fallback_id, fallback_region_type)
    logger.info(
        "Redfin search: %s  region_id=%s  region_type=%d",
        location,
        region["region_id"],
        region["region_type"],
    )

    params = {
        "al": 1,
        "market": "boston",
        "max_price": max_price,
        "min_price": min_price,
        "min_beds": min_beds,
        "min_baths": min_baths,
        "min_sqft": min_sqft,
        "num_beds": min_beds,
        "num_baths": min_baths,
        "region_id": region["region_id"],
        "region_type": region["region_type"],
        "sf": "1,2,3,5,6,7",
        "status": 9,
        "uipt": "1,2,3,4,5,6,7,8",
        "v": 8,
    }

    try:
        resp = requests.get(GIS_URL, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = _parse_rf_json(resp.text)
    except Exception as exc:
        logger.error("Redfin GIS request failed for %s: %s", location, exc)
        return listings

    homes = data.get("payload", {}).get("homes", [])
    logger.info("Redfin returned %d homes for %s", len(homes), location)
    if homes:
        sample = homes[0]
        logger.info(
            "Redfin sample home fields: price=%s beds=%s baths=%s sqft=%s",
            sample.get("price"), sample.get("beds"),
            sample.get("baths"), sample.get("sqFt"),
        )

    for home in homes:
        try:
            url_path = home.get("url", "")
            property_id = _extract_property_id(url_path) or str(
                home.get("propertyId", "")
            )
            listing_id = f"redfin_{property_id}"

            price = int(home.get("price", {}).get("value", 0) or 0)
            beds = int(home.get("beds", 0) or 0)
            baths = float(home.get("baths", 0) or 0)
            sqft = int(home.get("sqFt", {}).get("value", 0) or 0)
            address = home.get("address", {}).get("value", "Unknown Address")

            photo_urls = home.get("photoUrls", [])
            image_url = photo_urls[0] if photo_urls else None

            # Require price, beds, baths — sqft may be absent from GIS response
            # (API already filters by min_sqft so missing sqft = trust the API)
            if not all([price, beds, baths]):
                continue
            if not (min_price <= price <= max_price):
                continue
            if beds < min_beds or baths < min_baths:
                continue
            if sqft and sqft < min_sqft:
                continue

            has_office = False
            if beds == 4 and property_id:
                time.sleep(0.3)
                has_office = _check_has_office(property_id)

            listings.append(
                Listing(
                    id=listing_id,
                    address=address,
                    price=price,
                    beds=beds,
                    baths=baths,
                    sqft=sqft,
                    image_url=image_url,
                    redfin_url=f"{BASE_URL}{url_path}" if url_path else None,
                    has_office=has_office,
                    source="redfin",
                )
            )
        except Exception as exc:
            logger.debug("Error parsing Redfin home: %s", exc)

    return listings
