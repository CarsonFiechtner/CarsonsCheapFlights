"""Long-term price history as weekly summaries.

Individual prices are deleted after `raw_keep_days`, but before that every
finished week (Monday–Sunday, by the day prices were seen) is boiled down to
one row per (origin, destination, departure month) with count, min, 25th
percentile, median and max. These summaries are kept forever: in the
`route_weekly` table and as one CSV per week in `history_dir`. On GitHub that
directory is the `history` branch, so it survives even if the database is lost.

Each week's CSV is written once when the week ends and never changes.
"""

from __future__ import annotations

import csv
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS route_weekly (
    week_start   TEXT NOT NULL,   -- Monday, YYYY-MM-DD
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    depart_month TEXT NOT NULL,   -- YYYY-MM
    n            INTEGER NOT NULL,
    min_price    REAL NOT NULL,
    p25_price    REAL NOT NULL,
    median_price REAL NOT NULL,
    max_price    REAL NOT NULL,
    PRIMARY KEY (week_start, origin, destination, depart_month)
) WITHOUT ROWID;
"""

FIELDS = ["week_start", "origin", "destination", "depart_month", "n",
          "min_price", "p25_price", "median_price", "max_price"]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _summarize(prices: list[float]) -> tuple[int, float, float, float, float]:
    prices = sorted(prices)
    p25 = statistics.quantiles(prices, n=4, method="inclusive")[0] if len(prices) > 1 else prices[0]
    return len(prices), prices[0], round(p25, 2), round(statistics.median(prices), 2), prices[-1]


def rollup(conn: sqlite3.Connection, today: date | None = None) -> list[str]:
    """Summarize every finished week that isn't summarized yet. Returns the weeks added."""
    ensure_schema(conn)
    today = today or date.today()
    this_week = week_start(today)
    done = {r[0] for r in conn.execute("SELECT DISTINCT week_start FROM route_weekly")}
    days = [date.fromisoformat(r[0]) for r in
            conn.execute("SELECT DISTINCT seen_day FROM price_observations")]
    weeks = sorted({week_start(d) for d in days if week_start(d) < this_week} - {date.fromisoformat(w) for w in done})

    added = []
    for ws in weeks:
        groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        rows = conn.execute(
            "SELECT origin, destination, substr(depart_date, 1, 7), price FROM price_observations"
            " WHERE return_date != '' AND seen_day BETWEEN ? AND ?",
            (ws.isoformat(), (ws + timedelta(days=6)).isoformat()),
        )
        for origin, dest, month, price in rows:
            groups[(origin, dest, month)].append(price)
        conn.executemany(
            "INSERT OR IGNORE INTO route_weekly VALUES (?,?,?,?,?,?,?,?,?)",
            [(ws.isoformat(), o, d, m, *_summarize(p)) for (o, d, m), p in groups.items()],
        )
        added.append(ws.isoformat())
    conn.commit()
    return added


def export(conn: sqlite3.Connection, directory: str) -> list[Path]:
    """Write a CSV for each summarized week that doesn't have one yet."""
    ensure_schema(conn)
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for (ws,) in conn.execute("SELECT DISTINCT week_start FROM route_weekly ORDER BY week_start").fetchall():
        path = out / f"{ws}.csv"
        if path.exists():
            continue
        rows = conn.execute(
            f"SELECT {', '.join(FIELDS)} FROM route_weekly WHERE week_start = ?"
            " ORDER BY origin, destination, depart_month", (ws,))
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(FIELDS)
            w.writerows(rows)
        written.append(path)
    return written


def load(conn: sqlite3.Connection, directory: str) -> int:
    """Load weekly CSVs into the database (e.g. after the database was lost). Safe to repeat."""
    ensure_schema(conn)
    count = 0
    for path in sorted(Path(directory).glob("*.csv")):
        with path.open(newline="") as fh:
            rows = [tuple(r[f] for f in FIELDS) for r in csv.DictReader(fh)]
        conn.executemany("INSERT OR IGNORE INTO route_weekly VALUES (?,?,?,?,?,?,?,?,?)", rows)
        count += len(rows)
    conn.commit()
    return count
