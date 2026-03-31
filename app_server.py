"""
Mobile PWA server — serves the house-hunt app on your phone.

Usage:
    python app_server.py

Then open  http://<YOUR_COMPUTER_IP>:8080  in your phone browser
(both devices must be on the same WiFi), then tap
"Add to Home Screen" in Safari / Chrome to install it as an app.

For access from anywhere (not just home WiFi), see README for
free deployment options (Render.com, fly.io, etc.).
"""
import logging
import os
import socket

from flask import Flask, jsonify, send_from_directory

import database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="docs", static_url_path="")

PORT = int(os.getenv("APP_PORT", "8080"))


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("docs", "index.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory("docs", "manifest.json")


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory("docs", "sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/listings")
def api_listings():
    listings = database.get_all_listings()
    return jsonify(listings)


@app.route("/api/stats")
def api_stats():
    return jsonify(database.get_stats())


# ── Entry point ───────────────────────────────────────────────────────────────

def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "localhost"


if __name__ == "__main__":
    database.init_db()
    ip = _local_ip()
    logger.info("=" * 60)
    logger.info("House Hunt app running!")
    logger.info("  Local:   http://localhost:%d", PORT)
    logger.info("  Network: http://%s:%d   ← open this on your phone", ip, PORT)
    logger.info("  Tip: tap Share → 'Add to Home Screen' in Safari/Chrome")
    logger.info("=" * 60)
    app.run(host="0.0.0.0", port=PORT, debug=False)
