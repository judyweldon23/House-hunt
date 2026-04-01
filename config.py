"""Central configuration loaded from environment variables."""
# trigger scrape run
import os
from dotenv import load_dotenv

load_dotenv()

# ── Search criteria ────────────────────────────────────────────────────────────
SEARCH_CRITERIA = {
    "min_price": 2_200_000,
    "max_price": 3_500_000,
    "min_beds": 4,
    "min_baths": 3,
    "min_sqft": 3_500,
    "max_commute_minutes": 35,
    "commute_destination": "100 Clarendon St, Boston, MA 02116",
}

# ── Target locations ──────────────────────────────────────────────────────────
# region_type: 6=city, 2=neighborhood
# region_ids looked up at runtime via Redfin autocomplete; these are fallbacks.
LOCATIONS = [
    {"name": "Brookline, MA",  "redfin_fallback_id": "36099", "redfin_region_type": 6},
    # Newton city — region_id 11619 from redfin.com/city/11619/MA/Newton
    {"name": "Newton, MA",     "redfin_fallback_id": "11619", "redfin_region_type": 6},
    # Waban kept as a separate search (confirmed working region_id)
    {"name": "Waban, MA",      "redfin_fallback_id": "28762", "redfin_region_type": 6},
    {"name": "Wellesley, MA",  "redfin_fallback_id": "36180", "redfin_region_type": 6},
]

# ── Email ──────────────────────────────────────────────────────────────────────
EMAIL_CONFIG = {
    "recipient":      os.getenv("RECIPIENT_EMAIL", "judy.weldon@Baincapital.com"),
    "sender":         os.getenv("SENDER_EMAIL", ""),
    "smtp_host":      os.getenv("SMTP_HOST", "smtp.gmail.com"),
    "smtp_port":      int(os.getenv("SMTP_PORT") or "587"),
    "smtp_user":      os.getenv("SMTP_USER", ""),
    "smtp_password":  os.getenv("SMTP_PASSWORD", ""),
}

# ── Google Maps ────────────────────────────────────────────────────────────────
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# ── Schedule ───────────────────────────────────────────────────────────────────
SCRAPE_INTERVAL_DAYS = 2
