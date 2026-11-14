"""Client for the Travelpayouts (Aviasales) Data API.

These are *cached* prices that Aviasales users found recently, not live
searches. They're free and broad, which is what we want for finding deals; you
confirm the price yourself through the Google Flights link in the email.

Docs: https://support.travelpayouts.com/hc/en-us/articles/203956163
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import date

from .models import Fare

log = logging.getLogger(__name__)

BASE = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def month_strings(months_ahead: int, today: date | None = None) -> list[str]:
    today = today or date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(months_ahead):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def parse_response(payload: dict, origin: str, currency: str) -> list[Fare]:
    """Turn an API response into Fares, skipping anything malformed."""
    fares = []
    for item in payload.get("data") or []:
        try:
            ret = item.get("return_at")
            fares.append(Fare(
                origin=item.get("origin_airport") or item.get("origin") or origin,
                destination=item.get("destination") or item["destination_airport"],
                depart_date=item["departure_at"][:10],
                return_date=ret[:10] if ret else None,
                price=float(item["price"]),
                currency=payload.get("currency", currency),
                airline=item.get("airline"),
                stops=item.get("transfers"),
                return_stops=item.get("return_transfers"),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            log.debug("skipping malformed item %r: %s", item, exc)
    return fares


def _get(params: dict, token: str, retries: int = 3) -> dict:
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"X-Access-Token": token, "Accept-Encoding": "identity"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:  # network errors, 429s, 5xx
            if attempt == retries - 1:
                raise
            wait = 2 ** (attempt + 1)
            log.warning("request failed (%s); retrying in %ss", exc, wait)
            time.sleep(wait)
    return {}


def fetch_cheapest_anywhere(origin: str, month: str, token: str, currency: str = "usd",
                            limit: int = 1000) -> list[Fare]:
    """Cheapest cached round trips from `origin` to every destination in `month`."""
    params = {
        "origin": origin,
        "departure_at": month,
        "one_way": "false",
        "direct": "false",
        "unique": "false",   # several fares per destination, not just the single cheapest
        "sorting": "price",
        "currency": currency,
        "limit": limit,
        "page": 1,
    }
    payload = _get(params, token)
    if not payload.get("success", True):
        raise RuntimeError(f"Travelpayouts error for {origin} {month}: {payload}")
    return parse_response(payload, origin, currency)
