"""Long-term price history as monthly summaries.

Individual prices are deleted after `raw_keep_days`, but before that every
finished calendar month (by the day prices were seen) is boiled down to one
row per (origin, destination, departure month) with count, min, 25th
percentile, median and max. These summaries are kept forever: in the
`route_monthly` table and as one CSV per month in `history_dir`. On GitHub
that directory is on the `history` branch, so it survives even if the database
is lost. The monthly commit also counts as repository activity, which stops
GitHub from disabling the schedule after 60 idle days.

Each month's CSV is written once, after the month ends, and never changes.
"""

from __future__ import annotations

import csv
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS route_monthly (
    seen_month   TEXT NOT NULL,   -- YYYY-MM the prices were observed
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    depart_month TEXT NOT NULL,   -- YYYY-MM
    n            INTEGER NOT NULL,
    min_price    REAL NOT NULL,
    p25_price    REAL NOT NULL,
    median_price REAL NOT NULL,
    max_price    REAL NOT NULL,
    PRIMARY KEY (seen_month, origin, destination, depart_month)
) WITHOUT ROWID;
"""

FIELDS = ["seen_month", "origin", "destination", "depart_month", "n",
          "min_price", "p25_price", "median_price", "max_price"]

# Individual prices must outlive the longest month plus some slack, or a
# month could be partly deleted before it's summarized.
MIN_RAW_KEEP_DAYS = 40


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def month_of(day: date) -> str:
    return day.isoformat()[:7]


def month_bounds(month: str) -> tuple[date, date]:
    year, m = int(month[:4]), int(month[5:7])
    start = date(year, m, 1)
    next_start = date(year + 1, 1, 1) if m == 12 else date(year, m + 1, 1)
    return start, next_start - timedelta(days=1)


def _summarize(prices: list[float]) -> tuple[int, float, float, float, float]:
    prices = sorted(prices)
    p25 = statistics.quantiles(prices, n=4, method="inclusive")[0] if len(prices) > 1 else prices[0]
    return len(prices), prices[0], round(p25, 2), round(statistics.median(prices), 2), prices[-1]


def rollup(conn: sqlite3.Connection, today: date | None = None) -> list[str]:
    """Summarize every finished month that isn't summarized yet. Returns the months added."""
    ensure_schema(conn)
    current = month_of(today or date.today())
    done = {r[0] for r in conn.execute("SELECT DISTINCT seen_month FROM route_monthly")}
    seen = {r[0] for r in conn.execute("SELECT DISTINCT substr(seen_day, 1, 7) FROM price_observations")}
    todo = sorted(m for m in seen - done if m < current)

    for month in todo:
        start, end = month_bounds(month)
        groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        rows = conn.execute(
            "SELECT origin, destination, substr(depart_date, 1, 7), price FROM price_observations"
            " WHERE return_date != '' AND seen_day BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        )
        for origin, dest, depart_month, price in rows:
            groups[(origin, dest, depart_month)].append(price)
        conn.executemany(
            "INSERT OR IGNORE INTO route_monthly VALUES (?,?,?,?,?,?,?,?,?)",
            [(month, o, d, m, *_summarize(p)) for (o, d, m), p in groups.items()],
        )
    conn.commit()
    return todo


def export(conn: sqlite3.Connection, directory: str) -> list[Path]:
    """Write a CSV for each summarized month that doesn't have one yet."""
    ensure_schema(conn)
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for (month,) in conn.execute("SELECT DISTINCT seen_month FROM route_monthly ORDER BY seen_month").fetchall():
        path = out / f"{month}.csv"
        if path.exists():
            continue
        rows = conn.execute(
            f"SELECT {', '.join(FIELDS)} FROM route_monthly WHERE seen_month = ?"
            " ORDER BY origin, destination, depart_month", (month,))
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(FIELDS)
            w.writerows(rows)
        written.append(path)
    return written


def load(conn: sqlite3.Connection, directory: str) -> int:
    """Load monthly CSVs into the database (e.g. after the database was lost). Safe to repeat."""
    ensure_schema(conn)
    count = 0
    for path in sorted(Path(directory).glob("*.csv")):
        with path.open(newline="") as fh:
            rows = [tuple(r[f] for f in FIELDS) for r in csv.DictReader(fh)]
        conn.executemany("INSERT OR IGNORE INTO route_monthly VALUES (?,?,?,?,?,?,?,?,?)", rows)
        count += len(rows)
    conn.commit()
    return count
