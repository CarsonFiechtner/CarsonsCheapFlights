"""SQLite storage: the cheapest price seen each day for each itinerary, plus sent alerts.

Only one row per (route, dates, day) is kept, with the lowest price seen that
day, so scanning several times a day doesn't bloat the database.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from .models import Fare

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_observations (
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    depart_date  TEXT NOT NULL,   -- YYYY-MM-DD
    return_date  TEXT NOT NULL,   -- YYYY-MM-DD ('' for one-way)
    seen_day     TEXT NOT NULL,   -- YYYY-MM-DD (UTC) the price was observed
    price        REAL NOT NULL,
    currency     TEXT NOT NULL,
    airline      TEXT,
    stops        INTEGER,
    return_stops INTEGER,
    source       TEXT NOT NULL,
    PRIMARY KEY (origin, destination, depart_date, return_date, seen_day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_obs_seen ON price_observations (seen_day);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id           INTEGER PRIMARY KEY,
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    depart_month TEXT NOT NULL,  -- YYYY-MM
    price        REAL NOT NULL,
    baseline     REAL NOT NULL,
    sent_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alerts_route ON alerts_sent (origin, destination, depart_month);

CREATE TABLE IF NOT EXISTS problems_sent (
    day TEXT PRIMARY KEY            -- YYYY-MM-DD a "tracker problem" email went out
);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_fares(conn: sqlite3.Connection, fares: list[Fare], seen_day: str | None = None) -> int:
    """Save fares, keeping only the lowest price per itinerary per day."""
    seen_day = seen_day or date.today().isoformat()
    conn.executemany(
        """
        INSERT INTO price_observations (origin, destination, depart_date, return_date, seen_day,
            price, currency, airline, stops, return_stops, source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (origin, destination, depart_date, return_date, seen_day) DO UPDATE SET
            price = excluded.price, airline = excluded.airline, stops = excluded.stops,
            return_stops = excluded.return_stops, source = excluded.source
        WHERE excluded.price < price
        """,
        [(f.origin, f.destination, f.depart_date, f.return_date or "", seen_day, f.price,
          f.currency, f.airline, f.stops, f.return_stops, f.source) for f in fares],
    )
    conn.commit()
    return len(fares)


def prune(conn: sqlite3.Connection, keep_days: int) -> int:
    cur = conn.execute("DELETE FROM price_observations WHERE seen_day < date('now', ?)",
                       (f"-{keep_days} days",))
    conn.commit()
    return cur.rowcount


def _history_filter(origins: list[str] | str, destination: str, history_days: int,
                    depart_month: str | None, nights: tuple[int, int] | None) -> tuple[str, list]:
    if isinstance(origins, str):
        origins = [origins]
    where = [f"origin IN ({','.join('?' * len(origins))})", "destination = ?", "return_date != ''",
             "seen_day >= date('now', ?)"]
    params: list = [*origins, destination, f"-{history_days} days"]
    if depart_month:
        where.append("substr(depart_date, 1, 7) = ?")
        params.append(depart_month)
    if nights:
        where.append("julianday(return_date) - julianday(depart_date) BETWEEN ? AND ?")
        params.extend(nights)
    return " AND ".join(where), params


def route_prices(conn: sqlite3.Connection, origins: list[str] | str, destination: str,
                 history_days: int, depart_month: str | None = None,
                 nights: tuple[int, int] | None = None) -> list[float]:
    """Round-trip prices to `destination` from any of `origins` in the lookback window.

    One price per itinerary (dates) per day: the cheapest across the origins.
    That makes the history "best price from the home area", which is what a
    new fare from either airport gets compared against. Optionally limited to
    one departure month (YYYY-MM) and a trip length range in nights.
    """
    where, params = _history_filter(origins, destination, history_days, depart_month, nights)
    cur = conn.execute(
        f"SELECT MIN(price) AS price FROM price_observations WHERE {where}"
        " GROUP BY depart_date, return_date, seen_day", params)
    return [r["price"] for r in cur]


def route_history_days(conn: sqlite3.Connection, origins: list[str] | str, destination: str,
                       history_days: int, depart_month: str | None = None,
                       nights: tuple[int, int] | None = None) -> int:
    """How many different days we've seen matching round-trip prices to `destination` on."""
    where, params = _history_filter(origins, destination, history_days, depart_month, nights)
    return conn.execute(
        f"SELECT COUNT(DISTINCT seen_day) FROM price_observations WHERE {where}", params).fetchone()[0]


def last_alert(conn: sqlite3.Connection, origin: str, destination: str, depart_month: str):
    return conn.execute(
        "SELECT price, sent_at, julianday('now') - julianday(sent_at) AS age_days FROM alerts_sent"
        " WHERE origin = ? AND destination = ? AND depart_month = ?"
        " ORDER BY sent_at DESC, id DESC LIMIT 1",
        (origin, destination, depart_month),
    ).fetchone()


def record_alert(conn: sqlite3.Connection, origin: str, destination: str, depart_month: str,
                 price: float, baseline: float) -> None:
    conn.execute(
        "INSERT INTO alerts_sent (origin, destination, depart_month, price, baseline) VALUES (?,?,?,?,?)",
        (origin, destination, depart_month, price, baseline),
    )
    conn.commit()


def claim_problem_email(conn: sqlite3.Connection, day: str | None = None) -> bool:
    """True the first time it's called on a given day, so problem emails go out at most daily."""
    cur = conn.execute("INSERT OR IGNORE INTO problems_sent (day) VALUES (?)",
                       (day or date.today().isoformat(),))
    conn.commit()
    return cur.rowcount == 1
