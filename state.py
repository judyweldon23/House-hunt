"""Persist seen listing IDs so we never re-email the same property."""
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Set

from scrapers.base import Listing

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "seen_listings.json")


def _load() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"seen_ids": [], "last_run": None}
    except json.JSONDecodeError as exc:
        logger.warning("Corrupt state file — resetting: %s", exc)
        return {"seen_ids": [], "last_run": None}


def _save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def filter_new_listings(listings: List[Listing]) -> List[Listing]:
    """Return only listings whose IDs haven't been seen before."""
    state = _load()
    seen: Set[str] = set(state.get("seen_ids", []))
    new = [l for l in listings if l.id not in seen]
    logger.info(
        "%d listings total, %d already seen, %d new",
        len(listings), len(listings) - len(new), len(new),
    )
    return new


def mark_seen(listings: List[Listing]) -> None:
    """Persist newly seen listing IDs."""
    state = _load()
    seen: Set[str] = set(state.get("seen_ids", []))
    for l in listings:
        seen.add(l.id)
    state["seen_ids"] = sorted(seen)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    _save(state)
    logger.info("Marked %d listings as seen (total seen: %d)", len(listings), len(seen))
