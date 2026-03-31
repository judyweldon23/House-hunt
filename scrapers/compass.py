"""Compass scraper — parses JSON embedded in the search results page."""
import json
import logging
import re
import time
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from .base import Listing

logger = logging.getLogger(__name__)

BASE_URL = "https://www.compass.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Compass uses URL slugs for location
LOCATION_SLUGS = {
    "Brookline, MA": "brookline-ma",
    "Waban, MA":     "newton-ma",    # Waban is part of Newton
    "Wellesley, MA": "wellesley-ma",
}


def _build_search_url(slug: str, min_price: int, max_price: int,
                      min_beds: int, min_baths: int, min_sqft: int) -> str:
    # Compass URL filter format:  /price-min,max/beds-min+/baths-min+/sqft-min+/
    return (
        f"{BASE_URL}/homes-for-sale/{slug}/"
        f"price-{min_price},{max_price}/"
        f"beds-{min_beds}+/"
        f"baths-{min_baths}+/"
        f"sqft-{min_sqft}+/"
    )


def _parse_int(raw) -> int:
    if isinstance(raw, (int, float)):
        return int(raw)
    cleaned = re.sub(r"[^\d]", "", str(raw))
    return int(cleaned) if cleaned else 0


def _extract_from_script(html: str) -> list:
    """Try to find listing JSON inside <script> tags."""
    # Compass embeds listing data as window.__RELAY_STORE__ or similar
    patterns = [
        r'window\.__RELAY_STORE__\s*=\s*({.*?});\s*</script>',
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});\s*</script>',
        r'"listings"\s*:\s*(\[.*?\])',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                # Try to find an array of listings inside
                if isinstance(data, list):
                    return data
                # Traverse common paths
                for path in [
                    ["listings"],
                    ["listingResults", "listings"],
                    ["searchResults", "listings"],
                ]:
                    obj = data
                    for key in path:
                        obj = obj.get(key) if isinstance(obj, dict) else None
                        if obj is None:
                            break
                    if isinstance(obj, list):
                        return obj
            except (json.JSONDecodeError, AttributeError):
                continue
    return []


def scrape_compass(
    location: str,
    min_price: int,
    max_price: int,
    min_beds: int,
    min_baths: int,
    min_sqft: int,
) -> List[Listing]:
    """Return Compass listings matching criteria for one location."""
    listings: List[Listing] = []

    slug = LOCATION_SLUGS.get(location)
    if not slug:
        slug = location.lower().replace(", ", "-").replace(" ", "-")

    url = _build_search_url(slug, min_price, max_price, min_beds, min_baths, min_sqft)
    logger.info("Compass search: %s  url=%s", location, url)

    try:
        time.sleep(1.5)  # polite delay
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Compass request failed for %s: %s", location, exc)
        return listings

    raw = _extract_from_script(resp.text)

    if not raw:
        # Try scraping individual listing cards from HTML
        soup = BeautifulSoup(resp.text, "lxml")
        # Compass uses data-uw-listing-id attributes on cards
        cards = soup.select("[data-uw-listing-id], [data-listing-id]")
        if not cards:
            logger.warning(
                "Compass: could not parse listings for %s (possible bot block)", location
            )
        for card in cards:
            try:
                listing_id = card.get("data-uw-listing-id") or card.get("data-listing-id", "")
                price_el = card.select_one("[data-uw-rm-price], .listingCard-price")
                beds_el  = card.select_one("[data-uw-rm-beds],  .listingCard-beds")
                baths_el = card.select_one("[data-uw-rm-baths], .listingCard-baths")
                sqft_el  = card.select_one("[data-uw-rm-sqft],  .listingCard-sqft")
                addr_el  = card.select_one("[data-uw-rm-address], .listingCard-address")
                img_el   = card.select_one("img")
                link_el  = card.select_one("a[href]")

                price = _parse_int(price_el.get_text() if price_el else "0")
                beds  = _parse_int(beds_el.get_text()  if beds_el  else "0")
                baths = float(_parse_int(baths_el.get_text() if baths_el else "0"))
                sqft  = _parse_int(sqft_el.get_text()  if sqft_el  else "0")
                address = addr_el.get_text(strip=True) if addr_el else "Unknown"
                image_url = img_el.get("src") if img_el else None
                href = link_el.get("href", "") if link_el else ""
                full_url = f"{BASE_URL}{href}" if href.startswith("/") else href or None

                if not all([price, beds, sqft]):
                    continue
                if not (min_price <= price <= max_price):
                    continue
                if beds < min_beds or baths < min_baths or sqft < min_sqft:
                    continue

                listings.append(
                    Listing(
                        id=f"compass_{listing_id}",
                        address=address,
                        price=price,
                        beds=beds,
                        baths=baths,
                        sqft=sqft,
                        image_url=image_url,
                        compass_url=full_url,
                        source="compass",
                    )
                )
            except Exception as exc:
                logger.debug("Error parsing Compass card: %s", exc)
        return listings

    logger.info("Compass returned %d raw listings for %s", len(raw), location)
    for item in raw:
        try:
            listing_id = str(item.get("id") or item.get("listingId", ""))
            price  = _parse_int(item.get("price") or item.get("listPrice", 0))
            beds   = int(item.get("beds") or item.get("bedrooms", 0) or 0)
            baths  = float(item.get("baths") or item.get("totalBaths", 0) or 0)
            sqft   = _parse_int(item.get("sqft") or item.get("livingArea", 0))
            address = (
                item.get("address", {}).get("street", "Unknown")
                if isinstance(item.get("address"), dict)
                else str(item.get("address", "Unknown"))
            )
            image_url = (
                item.get("photos", [None])[0]
                if item.get("photos")
                else item.get("imageUrl")
            )
            slug_url = item.get("url") or item.get("listingUrl", "")
            full_url = (
                f"{BASE_URL}{slug_url}" if slug_url.startswith("/") else slug_url or None
            )

            if not all([price, beds, sqft]):
                continue
            if not (min_price <= price <= max_price):
                continue
            if beds < min_beds or baths < min_baths or sqft < min_sqft:
                continue

            listings.append(
                Listing(
                    id=f"compass_{listing_id}",
                    address=address,
                    price=price,
                    beds=beds,
                    baths=baths,
                    sqft=sqft,
                    image_url=image_url,
                    compass_url=full_url,
                    source="compass",
                )
            )
        except Exception as exc:
            logger.debug("Error parsing Compass listing: %s", exc)

    return listings
