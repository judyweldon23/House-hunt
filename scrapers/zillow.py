"""Zillow scraper — parses __NEXT_DATA__ JSON embedded in the search page."""
import json
import logging
import re
import time
import urllib.parse
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from .base import Listing

logger = logging.getLogger(__name__)

BASE_URL = "https://www.zillow.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

# Map friendly location names to Zillow city slugs
LOCATION_SLUGS = {
    "Brookline, MA": "Brookline-MA",
    "Waban, MA": "Newton-MA",       # Waban is a village within Newton
    "Wellesley, MA": "Wellesley-MA",
}


def _build_search_url(slug: str, min_price: int, max_price: int,
                      min_beds: int, min_baths: int, min_sqft: int) -> str:
    filter_state = {
        "price":              {"min": min_price, "max": max_price},
        "beds":               {"min": min_beds},
        "baths":              {"min": min_baths},
        "sqft":               {"min": min_sqft},
        "isForSaleByAgent":   {"value": True},
        "isForSaleByOwner":   {"value": False},
        "isNewConstruction":  {"value": False},
        "isComingSoon":       {"value": False},
        "isAuction":          {"value": False},
        "isForSaleForeclosure": {"value": False},
    }
    state = json.dumps(
        {"pagination": {}, "filterState": filter_state, "isListVisible": True},
        separators=(",", ":"),
    )
    encoded = urllib.parse.quote(state)
    return f"{BASE_URL}/homes/for_sale/{slug}_rb/?searchQueryState={encoded}"


def _parse_price(raw: str) -> int:
    """Convert '$2,500,000' or '2500000' to int."""
    cleaned = re.sub(r"[^\d]", "", str(raw))
    return int(cleaned) if cleaned else 0


def _parse_sqft(raw) -> int:
    if isinstance(raw, (int, float)):
        return int(raw)
    cleaned = re.sub(r"[^\d]", "", str(raw))
    return int(cleaned) if cleaned else 0


def _extract_listings_from_next_data(data: dict) -> list:
    """Navigate the Zillow __NEXT_DATA__ JSON to find listing rows."""
    try:
        page_props = data["props"]["pageProps"]
        # Two known locations depending on Zillow version
        for key in ("searchPageState", "initialReduxState"):
            if key in page_props:
                state = page_props[key]
                # path: cat1 -> searchResults -> listResults
                list_results = (
                    state.get("cat1", {})
                         .get("searchResults", {})
                         .get("listResults", [])
                )
                if list_results:
                    return list_results
    except (KeyError, TypeError):
        pass
    return []


def scrape_zillow(
    location: str,
    min_price: int,
    max_price: int,
    min_beds: int,
    min_baths: int,
    min_sqft: int,
) -> List[Listing]:
    """Return Zillow listings matching criteria for one location."""
    listings: List[Listing] = []

    slug = LOCATION_SLUGS.get(location)
    if not slug:
        # Derive slug from location name
        slug = location.replace(", ", "-").replace(" ", "-")

    url = _build_search_url(slug, min_price, max_price, min_beds, min_baths, min_sqft)
    logger.info("Zillow search: %s  url=%s", location, url)

    try:
        time.sleep(1.5)  # polite delay
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Zillow request failed for %s: %s", location, exc)
        return listings

    # Extract __NEXT_DATA__ JSON
    soup = BeautifulSoup(resp.text, "lxml")
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script_tag:
        logger.warning("Zillow: no __NEXT_DATA__ found for %s (possible bot block)", location)
        return listings

    try:
        next_data = json.loads(script_tag.string)
    except json.JSONDecodeError as exc:
        logger.warning("Zillow: failed to parse __NEXT_DATA__ for %s: %s", location, exc)
        return listings

    raw_listings = _extract_listings_from_next_data(next_data)
    logger.info("Zillow returned %d listings for %s", len(raw_listings), location)

    for item in raw_listings:
        try:
            zpid = str(item.get("zpid", ""))
            listing_id = f"zillow_{zpid}"

            price = _parse_price(item.get("price", 0))
            beds = int(item.get("beds", 0) or 0)
            baths = float(item.get("baths", 0) or 0)
            sqft = _parse_sqft(item.get("area", 0))
            address = item.get("address", "Unknown Address")
            image_url = item.get("imgSrc") or item.get("img")
            detail_url = item.get("detailUrl", "")
            full_url = (
                f"{BASE_URL}{detail_url}"
                if detail_url.startswith("/")
                else detail_url or None
            )

            if not all([price, beds, sqft]):
                continue
            if not (min_price <= price <= max_price):
                continue
            if beds < min_beds or baths < min_baths or sqft < min_sqft:
                continue

            listings.append(
                Listing(
                    id=listing_id,
                    address=address,
                    price=price,
                    beds=beds,
                    baths=baths,
                    sqft=sqft,
                    image_url=image_url,
                    zillow_url=full_url,
                    source="zillow",
                )
            )
        except Exception as exc:
            logger.debug("Error parsing Zillow listing: %s", exc)

    return listings
