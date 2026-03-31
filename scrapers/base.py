"""Shared Listing dataclass used by all scrapers."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Listing:
    id: str
    address: str
    price: int
    beds: int
    baths: float
    sqft: int
    image_url: Optional[str] = None
    redfin_url: Optional[str] = None
    zillow_url: Optional[str] = None
    compass_url: Optional[str] = None
    commute_minutes: Optional[int] = None
    has_office: bool = False
    source: str = ""

    @property
    def preferred_url(self) -> Optional[str]:
        """Return Redfin URL first, then Compass, then Zillow."""
        return self.redfin_url or self.compass_url or self.zillow_url

    @property
    def preferred_source_label(self) -> str:
        if self.redfin_url:
            return "Redfin"
        if self.compass_url:
            return "Compass"
        if self.zillow_url:
            return "Zillow"
        return "View Listing"

    @property
    def priority_score(self) -> int:
        """Higher = more desirable. 5 BR+ = 2, 4 BR + office = 1, else 0."""
        if self.beds >= 5:
            return 2
        if self.beds == 4 and self.has_office:
            return 1
        return 0

    @property
    def formatted_price(self) -> str:
        return f"${self.price:,.0f}"

    @property
    def formatted_baths(self) -> str:
        return f"{self.baths:.1f}".rstrip("0").rstrip(".")
