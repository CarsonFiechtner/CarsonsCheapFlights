"""Decide which fares are deals, compared to what the destination normally costs.

All origins in the config (SFO and SJC by default) count as one home area:
- the "normal price" for a destination is the cheapest price from any home
  airport, per itinerary, per day;
- there's at most one deal per destination and departure month, using the
  cheapest airport, with the other airport listed as an alternative.
"""

from __future__ import annotations

import sqlite3
import statistics

from . import db
from .config import Config
from .models import Deal, Fare


def route_stats(prices: list[float]) -> tuple[float, float] | None:
    """(median, 25th percentile), or None if there's no data."""
    if not prices:
        return None
    if len(prices) == 1:
        return prices[0], prices[0]
    p25 = statistics.quantiles(prices, n=4, method="inclusive")[0]
    return statistics.median(prices), p25


def passes_quality(fare: Fare, cfg: Config) -> bool:
    if fare.return_date is None:
        return False  # round trips only for now; one-way prices aren't comparable
    if not cfg.min_trip_nights <= fare.nights <= cfg.max_trip_nights:
        return False  # 1-night or 2-month "trips" aren't useful even when cheap
    for s in (fare.stops, fare.return_stops):
        if s is not None and s > cfg.max_stops:
            return False
    return True


def evaluate(fare: Fare, prices: list[float], cfg: Config, days_seen: int | None = None) -> Deal | None:
    """`days_seen` is how many different days the prices came from. One scan has
    many dates per destination, so without this a single scan would look like
    plenty of history and the cheapest dates would be flagged against the dearest."""
    stats = route_stats(prices)
    if stats is None or len(prices) < cfg.min_samples:
        return None
    if days_seen is not None and days_seen < cfg.min_history_days:
        return None
    median, p25 = stats
    if median <= 0:
        return None
    pct_off = 1 - fare.price / median
    if pct_off < cfg.min_pct_off or median - fare.price < cfg.min_dollars_off or fare.price > p25:
        return None
    return Deal(fare=fare, baseline=median, p25=p25, samples=len(prices),
                is_mistake=pct_off >= cfg.mistake_pct_off)


def should_alert(conn: sqlite3.Connection, deal: Deal, cfg: Config) -> bool:
    prev = db.last_alert(conn, cfg.origin_label(), deal.fare.destination, deal.fare.depart_month)
    if prev is None:
        return True
    if prev["age_days"] >= cfg.realert_days:
        return True
    return deal.fare.price <= prev["price"] * (1 - cfg.realert_drop)


def find_deals(conn: sqlite3.Connection, fares: list[Fare], cfg: Config) -> list[Deal]:
    """Best new deal per (destination, departure month) across home airports, best first."""
    # Cheapest qualifying deal per (destination, month, origin).
    per_origin: dict[tuple[str, str], dict[str, Deal]] = {}
    cache: dict[tuple[str, str | None], tuple[list[float], int]] = {}

    def history(dest: str, month: str | None) -> tuple[list[float], int]:
        key = (dest, month)
        if key not in cache:
            cache[key] = (
                db.route_prices(conn, cfg.origins, dest, cfg.history_days, month, cfg.nights),
                db.route_history_days(conn, cfg.origins, dest, cfg.history_days, month, cfg.nights),
            )
        return cache[key]

    for fare in fares:
        if not passes_quality(fare, cfg):
            continue
        # Prefer the normal price for the same travel month (fares are seasonal);
        # fall back to all months only until that month has enough history.
        month_prices, month_days = history(fare.destination, fare.depart_month)
        if len(month_prices) >= cfg.min_samples and month_days >= cfg.min_history_days:
            deal = evaluate(fare, month_prices, cfg, month_days)
            if deal:
                deal.baseline_month = fare.depart_month
        else:
            all_prices, all_days = history(fare.destination, None)
            deal = evaluate(fare, all_prices, cfg, all_days)
        if deal is None:
            continue
        group = per_origin.setdefault((fare.destination, fare.depart_month), {})
        cur = group.get(fare.origin)
        if cur is None or fare.price < cur.fare.price:
            group[fare.origin] = deal

    deals = []
    for group in per_origin.values():
        ranked = sorted(group.values(), key=lambda d: d.fare.price)
        best = ranked[0]
        best.alternatives = [d.fare for d in ranked[1:]]
        if should_alert(conn, best, cfg):
            deals.append(best)
    return sorted(deals, key=lambda d: (not d.is_mistake, -d.pct_off))
