# House Hunt — Automated Listing Alerts

Scrapes **Redfin**, **Zillow**, and **Compass** every 2 days for homes matching your criteria in **Brookline**, **Waban**, and **Wellesley, MA**, then emails a formatted HTML report to `judy.weldon@Baincapital.com`.

## Search Criteria
| Field | Value |
|---|---|
| Price | $2,200,000 – $3,500,000 |
| Bedrooms | 4+ (priority: 5+ or 4 BR + office) |
| Bathrooms | 3+ |
| Square footage | 3,500+ sq ft |
| Max commute | 35 min driving to 100 Clarendon St, Boston at 8 am Wednesday |

## Setup

### 1. Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables
```bash
cp .env.example .env
# Edit .env and fill in all values
```

Required variables:
- `SENDER_EMAIL` / `SMTP_USER` / `SMTP_PASSWORD` — Gmail account used to send alerts
  - For Gmail, create an [App Password](https://support.google.com/accounts/answer/185833) (not your regular password)
- `GOOGLE_MAPS_API_KEY` — Enable **Distance Matrix API** in [Google Cloud Console](https://console.cloud.google.com)
  - Without this key, all listings that pass price/beds/baths/sqft criteria are included in the email (commute not filtered)

### 3. Test a single run
```bash
# Run once and send email immediately (ignores "seen" deduplication)
python main.py --force
```

### 4. Start the recurring scheduler (every 2 days)
```bash
python scheduler.py
```

Keep this process running. Use `tmux`, `screen`, or a systemd service for persistence.

#### systemd example
```ini
# /etc/systemd/system/house-hunt.service
[Unit]
Description=House Hunt Scraper
After=network.target

[Service]
WorkingDirectory=/path/to/house-hunt
ExecStart=/path/to/.venv/bin/python scheduler.py
Restart=on-failure
EnvironmentFile=/path/to/house-hunt/.env

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now house-hunt
```

## Project Structure
```
house-hunt/
├── config.py           # Search criteria, SMTP, and API key config
├── main.py             # Orchestrator: scrape → dedup → commute → email
├── scheduler.py        # APScheduler-based 2-day scheduler
├── commute.py          # Google Maps Distance Matrix commute check
├── emailer.py          # HTML email builder + SMTP sender
├── state.py            # Seen-listings deduplication (data/seen_listings.json)
├── scrapers/
│   ├── base.py         # Listing dataclass
│   ├── redfin.py       # Redfin GIS API scraper (primary source)
│   ├── zillow.py       # Zillow HTML scraper (supplementary)
│   └── compass.py      # Compass HTML scraper (supplementary)
├── requirements.txt
└── .env.example
```

## Notes
- **Redfin** is the most reliable source and is preferred for listing links.
- Zillow and Compass have anti-bot measures; if blocked they are skipped gracefully and Redfin results are still emailed.
- The `data/seen_listings.json` file tracks which listings have already been emailed so you don't receive duplicates.
- Office detection (for 4 BR + office priority) fetches Redfin's listing detail API for each 4 BR home. This adds a small delay but stays within polite rate limits.
