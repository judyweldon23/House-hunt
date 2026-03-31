"""HTML email builder and SMTP sender."""
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

import pytz

from scrapers.base import Listing

logger = logging.getLogger(__name__)

EASTERN = pytz.timezone("America/New_York")


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _commute_text(listing: Listing) -> str:
    if listing.commute_minutes is None:
        return "Commute: not checked"
    return f"&#128663; {listing.commute_minutes} min to 100 Clarendon St (8&nbsp;am&nbsp;Wed)"


def _priority_badge(listing: Listing) -> str:
    if listing.beds >= 5:
        return '<span class="badge badge-5br">&#11088; 5+ Bedrooms</span>'
    if listing.has_office:
        return '<span class="badge badge-office">&#128188; 4 BR + Office</span>'
    return ""


def _listing_card(listing: Listing) -> str:
    image_block = ""
    if listing.image_url:
        image_block = (
            f'<img class="listing-img" src="{listing.image_url}" '
            f'alt="{listing.address}" />'
        )
    else:
        image_block = '<div class="no-img">No photo available</div>'

    badge = _priority_badge(listing)
    commute = _commute_text(listing)
    url = listing.preferred_url or "#"
    source_label = listing.preferred_source_label

    # Additional source links
    extra_links = []
    if listing.redfin_url and source_label != "Redfin":
        extra_links.append(f'<a class="alt-link" href="{listing.redfin_url}">Redfin</a>')
    if listing.zillow_url and source_label != "Zillow":
        extra_links.append(f'<a class="alt-link" href="{listing.zillow_url}">Zillow</a>')
    if listing.compass_url and source_label != "Compass":
        extra_links.append(f'<a class="alt-link" href="{listing.compass_url}">Compass</a>')
    alt_links_html = (
        " &bull; ".join(["Also on:"] + extra_links) if extra_links else ""
    )

    priority_class = "priority" if listing.priority_score > 0 else ""

    return f"""
    <div class="listing-card {priority_class}">
      {image_block}
      <div class="listing-body">
        {badge}
        <div class="listing-price">{listing.formatted_price}</div>
        <div class="listing-address">{listing.address}</div>
        <div class="listing-stats">
          <div class="stat">
            <div class="stat-value">{listing.beds}</div>
            <div class="stat-label">Beds</div>
          </div>
          <div class="stat">
            <div class="stat-value">{listing.formatted_baths}</div>
            <div class="stat-label">Baths</div>
          </div>
          <div class="stat">
            <div class="stat-value">{listing.sqft:,}</div>
            <div class="stat-label">Sq&nbsp;Ft</div>
          </div>
        </div>
        <div class="commute-row">{commute}</div>
        <div class="link-row">
          <a class="main-link" href="{url}">View on {source_label}</a>
          <span class="alt-links">{alt_links_html}</span>
        </div>
      </div>
    </div>
    """


CSS = """
  body { margin:0; padding:0; background:#f0f2f5; font-family:Arial,Helvetica,sans-serif; }
  .wrapper { max-width:700px; margin:0 auto; padding:20px; }
  .header { background:#1a3a5c; color:#fff; padding:24px 20px; border-radius:8px 8px 0 0; text-align:center; }
  .header h1 { margin:0 0 6px; font-size:22px; }
  .header p  { margin:0; font-size:14px; opacity:0.85; }
  .section-title { font-size:16px; font-weight:bold; color:#1a3a5c; margin:20px 0 10px; padding-left:4px; border-left:4px solid #e67e22; padding-left:10px; }
  .listing-card { background:#fff; border-radius:8px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,.1); overflow:hidden; }
  .listing-card.priority { border-top:4px solid #e67e22; }
  .listing-img { width:100%; max-height:260px; object-fit:cover; display:block; }
  .no-img { width:100%; height:160px; background:#dde3eb; display:flex; align-items:center; justify-content:center; color:#888; font-size:14px; }
  .listing-body { padding:16px; }
  .badge { display:inline-block; font-size:12px; padding:3px 9px; border-radius:20px; margin-bottom:8px; }
  .badge-5br { background:#fff3cd; color:#856404; }
  .badge-office { background:#d1ecf1; color:#0c5460; }
  .listing-price { font-size:26px; font-weight:bold; color:#1a3a5c; }
  .listing-address { color:#555; font-size:14px; margin:4px 0 12px; }
  .listing-stats { display:flex; gap:20px; margin-bottom:12px; }
  .stat { text-align:center; min-width:60px; }
  .stat-value { font-size:18px; font-weight:bold; color:#222; }
  .stat-label { font-size:11px; color:#888; text-transform:uppercase; letter-spacing:.5px; }
  .commute-row { background:#e8f5e9; color:#2e7d32; font-size:13px; padding:7px 10px; border-radius:4px; margin-bottom:12px; display:inline-block; }
  .link-row { display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  .main-link { background:#1a3a5c; color:#fff; padding:9px 18px; border-radius:5px; text-decoration:none; font-size:14px; font-weight:bold; }
  .alt-links { font-size:13px; color:#555; }
  .alt-link { color:#1a3a5c; text-decoration:none; }
  .alt-link:hover { text-decoration:underline; }
  .footer { text-align:center; color:#aaa; font-size:12px; padding:20px 0 8px; }
  .no-listings { background:#fff; border-radius:8px; padding:30px; text-align:center; color:#888; }
"""


def build_html_email(listings: List[Listing]) -> str:
    """Build the full HTML email body."""
    now = datetime.now(EASTERN).strftime("%B %d, %Y")
    count = len(listings)

    # Split into priority and standard sections
    priority = sorted(
        [l for l in listings if l.priority_score > 0],
        key=lambda l: (-l.priority_score, l.price),
    )
    standard = sorted(
        [l for l in listings if l.priority_score == 0],
        key=lambda l: l.price,
    )

    def section(title: str, items: List[Listing]) -> str:
        if not items:
            return ""
        cards = "".join(_listing_card(l) for l in items)
        return f'<div class="section-title">{title}</div>{cards}'

    body_sections = ""
    if priority:
        body_sections += section("&#11088; Priority Listings (5 BR+ or 4 BR + Office)", priority)
    if standard:
        body_sections += section("All Matching Listings", standard)

    if not listings:
        body_sections = '<div class="no-listings">No new listings matching your criteria this run.</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>House Hunt Results — {now}</title>
  <style>{CSS}</style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>&#127968; House Hunt Results</h1>
      <p>{now} &mdash; {count} new listing{'s' if count != 1 else ''} in Brookline, Waban &amp; Wellesley, MA</p>
      <p style="font-size:12px;margin-top:6px;opacity:.7;">
        Criteria: $2.2M&ndash;$3.5M &bull; 4+ beds &bull; 3+ baths &bull; 3,500+ sq ft
        &bull; &le;35 min commute to 100 Clarendon St at 8&nbsp;am&nbsp;Wed
      </p>
    </div>
    {body_sections}
    <div class="footer">
      This alert was generated automatically. Listings scrape every 2 days from Redfin, Zillow, and Compass.
    </div>
  </div>
</body>
</html>"""


def send_email(
    listings: List[Listing],
    smtp_config: dict,
    subject_suffix: str = "",
) -> bool:
    """
    Build and send the HTML email.

    smtp_config keys: recipient, sender, smtp_host, smtp_port, smtp_user, smtp_password
    Returns True on success.
    """
    now = datetime.now(EASTERN).strftime("%B %d, %Y")
    count = len(listings)
    subject = f"House Hunt: {count} New Listing{'s' if count != 1 else ''} — {now}{subject_suffix}"

    html_body = build_html_email(listings)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_config["sender"]
    msg["To"]      = smtp_config["recipient"]

    # Plain-text fallback
    plain_lines = [f"House Hunt Results — {now}", f"{count} new listings\n"]
    for l in listings:
        plain_lines.append(
            f"{l.address} | {l.formatted_price} | {l.beds}bd {l.formatted_baths}ba "
            f"| {l.sqft:,} sqft | {l.preferred_url}"
        )
    plain_body = "\n".join(plain_lines)

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_config["smtp_host"], smtp_config["smtp_port"]) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_config["smtp_user"], smtp_config["smtp_password"])
            server.sendmail(
                smtp_config["sender"],
                smtp_config["recipient"],
                msg.as_string(),
            )
        logger.info("Email sent to %s (%d listings)", smtp_config["recipient"], count)
        return True
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False
