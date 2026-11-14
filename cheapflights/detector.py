"""Decide which fares are deals, compared to what the route normally costs."""

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
    for s in (fare.stops, fare.return_stops):
        if s is not None and s > cfg.max_stops:
            return False
    return True


def evaluate(fare: Fare, prices: list[float], cfg: Config) -> Deal | None:
    stats = route_stats(prices)
    if stats is None or len(prices) < cfg.min_samples:
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
    prev = db.last_alert(conn, deal.fare.origin, deal.fare.destination, deal.fare.depart_month)
    if prev is None:
        return True
    if prev["age_days"] >= cfg.realert_days:
        return True
    return deal.fare.price <= prev["price"] * (1 - cfg.realert_drop)


def find_deals(conn: sqlite3.Connection, fares: list[Fare], cfg: Config) -> list[Deal]:
    """Best new deal per (origin, destination, departure month), best first."""
    best: dict[tuple[str, str, str], Deal] = {}
    cache: dict[tuple[str, str], list[float]] = {}
    for fare in fares:
        if not passes_quality(fare, cfg):
            continue
        key = (fare.origin, fare.destination)
        if key not in cache:
            cache[key] = db.route_prices(conn, fare.origin, fare.destination, cfg.history_days)
        deal = evaluate(fare, cache[key], cfg)
        if deal is None:
            continue
        k = (fare.origin, fare.destination, fare.depart_month)
        if k not in best or deal.fare.price < best[k].fare.price:
            best[k] = deal
    deals = [d for d in best.values() if should_alert(conn, d, cfg)]
    return sorted(deals, key=lambda d: (not d.is_mistake, -d.pct_off))
